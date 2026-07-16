#!/bin/zsh
set -euo pipefail

ROOT=${0:A:h}
SDK=${ANDROID_HOME:-$HOME/Library/Android/sdk}
BUILD_TOOLS=${BUILD_TOOLS:-$SDK/build-tools/37.0.0}
PLATFORM=${PLATFORM:-$SDK/platforms/android-34/android.jar}
JDK=${JAVA_HOME:-/Applications/Android Studio.app/Contents/jbr/Contents/Home}
OUT=$ROOT/build
export JAVA_HOME=$JDK
export PATH="$JDK/bin:$PATH"

rm -rf "$OUT"
mkdir -p "$OUT/classes" "$OUT/test-classes" "$OUT/dex"

"$BUILD_TOOLS/aapt2" link \
  -I "$PLATFORM" \
  --manifest "$ROOT/AndroidManifest.xml" \
  -o "$OUT/event-bridge-unsigned.apk"

"$JDK/bin/javac" \
  -source 8 -target 8 \
  -classpath "$PLATFORM" \
  -d "$OUT/classes" \
  "$ROOT/src/com/tikpoc/bridge/TikTokNotificationClassifier.java" \
  "$ROOT/src/com/tikpoc/bridge/TikTokNotificationService.java" \
  "$ROOT/src/com/tikpoc/bridge/ConfigReceiver.java"

"$JDK/bin/javac" \
  -source 8 -target 8 \
  -classpath "$OUT/classes" \
  -d "$OUT/test-classes" \
  "$ROOT/test/com/tikpoc/bridge/TikTokNotificationClassifierTest.java"

"$JDK/bin/java" -cp "$OUT/classes:$OUT/test-classes" \
  com.tikpoc.bridge.TikTokNotificationClassifierTest

"$BUILD_TOOLS/d8" \
  --lib "$PLATFORM" \
  --output "$OUT/dex" \
  $(find "$OUT/classes" -name '*.class' -print)

cd "$OUT/dex"
zip -q "$OUT/event-bridge-unsigned.apk" classes.dex

KEYSTORE=$ROOT/debug.keystore
if [[ ! -f "$KEYSTORE" ]]; then
  "$JDK/bin/keytool" -genkeypair -noprompt \
    -keystore "$KEYSTORE" -storepass android -keypass android \
    -alias androiddebugkey -dname 'CN=Android Debug,O=Android,C=US' \
    -keyalg RSA -keysize 2048 -validity 10000
fi

"$BUILD_TOOLS/apksigner" sign \
  --ks "$KEYSTORE" --ks-pass pass:android --key-pass pass:android \
  --out "$OUT/event-bridge.apk" "$OUT/event-bridge-unsigned.apk"

"$BUILD_TOOLS/apksigner" verify "$OUT/event-bridge.apk"
echo "$OUT/event-bridge.apk"
