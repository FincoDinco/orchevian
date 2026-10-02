#!/usr/bin/env bash
# Prepare a hosted macOS runner to sign and notarize: import the Developer ID
# certificate into a temporary keychain, write the notarization key, and export the
# variables scripts/sign_macos.py reads. Hosted runners are discarded after each job.
set -euo pipefail

if [[ -z "${MACOS_SIGNING_P12_BASE64:-}" ]]; then
  if [[ "${REQUIRE_SIGNING:-}" == "true" ]]; then
    echo "::error::The Apple signing secrets are not available to this run."
    exit 1
  fi
  # Pull requests from forks and Dependabot get no secrets; they build unsigned.
  echo "::notice::No Apple signing secrets in this run; building unsigned."
  exit 0
fi

keychain="$RUNNER_TEMP/signing.keychain-db"
password=$(uuidgen)
security create-keychain -p "$password" "$keychain"
# Stay unlocked for six hours: the build waits on two notarizations.
security set-keychain-settings -lut 21600 "$keychain"
security unlock-keychain -p "$password" "$keychain"
certificate="$RUNNER_TEMP/signing.p12"
printf '%s' "$MACOS_SIGNING_P12_BASE64" | base64 --decode > "$certificate"
security import "$certificate" -k "$keychain" -f pkcs12 -P "$MACOS_SIGNING_P12_PASSWORD" \
  -T /usr/bin/codesign
rm "$certificate"
# Apple issues Developer ID certificates from its G2 intermediate, which macOS does not
# ship (Xcode adds it). Without it the identity is invalid and codesign cannot use it.
# A .p12 exported from Keychain Access usually includes it already.
g2=F16CD3C54C7F83CEA4BF1A3E6A0819C8AAA8E4A1528FD144715F350643D2DF3A
if ! security find-certificate -a -Z "$keychain" | grep -q "SHA-256 hash: $g2"; then
  intermediate="$RUNNER_TEMP/DeveloperIDG2CA.cer"
  curl -fsSL -o "$intermediate" https://www.apple.com/certificateauthority/DeveloperIDG2CA.cer
  echo "$g2  $intermediate" | shasum -a 256 -c -
  security import "$intermediate" -k "$keychain"
fi
# Let codesign use the key without a confirmation dialog.
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$password" \
  "$keychain" > /dev/null
# Search the new keychain alongside the runner's own.
read -r -a existing <<< "$(security list-keychains -d user | tr -d '"' | xargs)"
security list-keychains -d user -s "$keychain" "${existing[@]}"
identity=$(security find-identity -v -p codesigning "$keychain" \
  | awk '/Developer ID Application/ { print $2; exit }')
if [[ -z "$identity" ]]; then
  # Without -v this also lists identities that are present but not valid, and why.
  security find-identity -p codesigning "$keychain"
  echo "::error::The certificate has no valid Developer ID Application identity."
  exit 1
fi

key="$RUNNER_TEMP/notary-key.p8"
# Accept either the key file's text or its base64 encoding.
if [[ "$NOTARY_API_KEY_P8" == *"BEGIN PRIVATE KEY"* ]]; then
  printf '%s\n' "$NOTARY_API_KEY_P8" > "$key"
else
  printf '%s' "$NOTARY_API_KEY_P8" | base64 --decode > "$key"
fi
{
  echo "MACOS_SIGNING_IDENTITY=$identity"
  echo "NOTARY_API_KEY_PATH=$key"
  echo "NOTARY_API_KEY_ID=$NOTARY_API_KEY_ID"
  echo "NOTARY_API_ISSUER_ID=$NOTARY_API_ISSUER_ID"
} >> "$GITHUB_ENV"
echo "Signing as Developer ID $identity"
