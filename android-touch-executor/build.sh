#!/bin/zsh
set -euo pipefail

ROOT=$(cd "$(dirname "$0")" && pwd)
SDK=${ANDROID_HOME:-$HOME/Library/Android/sdk}
BUILD_TOOLS=${BUILD_TOOLS:-$SDK/build-tools/37.0.0}
PLATFORM=${PLATFORM:-$SDK/platforms/android-34/android.jar}
JDK=${JAVA_HOME:-/Applications/Android Studio.app/Contents/jbr/Contents/Home}
OUT=$ROOT/build
export JAVA_HOME=$JDK
export PATH="$JDK/bin:$PATH"

rm -rf "$OUT"
mkdir -p "$OUT/classes" "$OUT/test-classes" "$OUT/dex" "$OUT/compiled-res"

"$JDK/bin/javac" \
  -source 8 -target 8 \
  -classpath "$PLATFORM" \
  -d "$OUT/classes" \
  "$ROOT/src/com/tikpoc/touch/Protocol.java" \
  "$ROOT/src/com/tikpoc/touch/SemanticSnapshot.java" \
  "$ROOT/src/com/tikpoc/touch/TikTokSemantics.java" \
  "$ROOT/src/com/tikpoc/touch/TikTokInterruptionSemantics.java" \
  "$ROOT/src/com/tikpoc/touch/TikTokSearchSemantics.java" \
  "$ROOT/src/com/tikpoc/touch/CommandGate.java" \
  "$ROOT/src/com/tikpoc/touch/TouchCommandDispatcher.java" \
  "$ROOT/src/com/tikpoc/touch/LoopbackCommandServer.java" \
  "$ROOT/src/com/tikpoc/touch/DeviceTaskStore.java" \
  "$ROOT/src/com/tikpoc/touch/AndroidTaskBackend.java" \
  "$ROOT/src/com/tikpoc/touch/DeviceApiClient.java" \
  "$ROOT/src/com/tikpoc/touch/DeviceProvisioning.java" \
  "$ROOT/src/com/tikpoc/touch/AndroidProvisioningStore.java" \
  "$ROOT/src/com/tikpoc/touch/AndroidTokenVault.java" \
  "$ROOT/src/com/tikpoc/touch/ProvisioningActivity.java" \
  "$ROOT/src/com/tikpoc/touch/SessionPacingPlanner.java" \
  "$ROOT/src/com/tikpoc/touch/AutonomousTaskRunner.java" \
  "$ROOT/src/com/tikpoc/touch/AutonomousTaskExecutor.java" \
  "$ROOT/src/com/tikpoc/touch/BrowseGestureGeometry.java" \
  "$ROOT/src/com/tikpoc/touch/AccessibilityUiAdapter.java" \
  "$ROOT/src/com/tikpoc/touch/CommentTaskExecutor.java" \
  "$ROOT/src/com/tikpoc/touch/AutonomousStartupRecovery.java" \
  "$ROOT/src/com/tikpoc/touch/TikPocAccessibilityService.java"

"$JDK/bin/javac" \
  -source 8 -target 8 \
  -classpath "$PLATFORM:$OUT/classes" \
  -d "$OUT/test-classes" \
  "$ROOT/test/com/tikpoc/touch/ProtocolTest.java" \
  "$ROOT/test/com/tikpoc/touch/TikTokSemanticsTest.java" \
  "$ROOT/test/com/tikpoc/touch/TikTokInterruptionSemanticsTest.java" \
  "$ROOT/test/com/tikpoc/touch/TikTokSearchSemanticsTest.java" \
  "$ROOT/test/com/tikpoc/touch/CommandGateTest.java" \
  "$ROOT/test/com/tikpoc/touch/TouchCommandDispatcherTest.java" \
  "$ROOT/test/com/tikpoc/touch/LoopbackCommandServerTest.java" \
  "$ROOT/test/com/tikpoc/touch/DeviceTaskStoreTest.java" \
  "$ROOT/test/com/tikpoc/touch/AndroidTaskBackendTest.java" \
  "$ROOT/test/com/tikpoc/touch/DeviceApiClientTest.java" \
  "$ROOT/test/com/tikpoc/touch/DeviceProvisioningTest.java" \
  "$ROOT/test/com/tikpoc/touch/SessionPacingPlannerTest.java" \
  "$ROOT/test/com/tikpoc/touch/AutonomousTaskRunnerTest.java" \
  "$ROOT/test/com/tikpoc/touch/AutonomousTaskExecutorTest.java" \
  "$ROOT/test/com/tikpoc/touch/BrowseGestureGeometryTest.java" \
  "$ROOT/test/com/tikpoc/touch/AccessibilityUiAdapterTest.java" \
  "$ROOT/test/com/tikpoc/touch/CommentTaskExecutorTest.java" \
  "$ROOT/test/com/tikpoc/touch/AutonomousStartupRecoveryTest.java"

"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.ProtocolTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.TikTokSemanticsTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.TikTokInterruptionSemanticsTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.TikTokSearchSemanticsTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.CommandGateTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.TouchCommandDispatcherTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.LoopbackCommandServerTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.DeviceTaskStoreTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.AndroidTaskBackendTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.DeviceApiClientTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.DeviceProvisioningTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.SessionPacingPlannerTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.AutonomousTaskRunnerTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.AutonomousTaskExecutorTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.BrowseGestureGeometryTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.AccessibilityUiAdapterTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.CommentTaskExecutorTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.AutonomousStartupRecoveryTest

"$BUILD_TOOLS/aapt2" compile \
  --dir "$ROOT/res" \
  -o "$OUT/compiled-res"

"$BUILD_TOOLS/aapt2" link \
  -I "$PLATFORM" \
  --manifest "$ROOT/AndroidManifest.xml" \
  -R "$OUT/compiled-res/xml_accessibility_service.xml.flat" \
  -o "$OUT/touch-executor-unsigned.apk"

"$BUILD_TOOLS/d8" \
  --lib "$PLATFORM" \
  --output "$OUT/dex" \
  $(find "$OUT/classes" -name '*.class' -print)

cd "$OUT/dex"
zip -q "$OUT/touch-executor-unsigned.apk" classes.dex

KEYSTORE=$ROOT/debug.keystore
if [[ ! -f "$KEYSTORE" ]]; then
  "$JDK/bin/keytool" -genkeypair -noprompt \
    -keystore "$KEYSTORE" -storepass android -keypass android \
    -alias androiddebugkey -dname 'CN=Android Debug,O=Android,C=US' \
    -keyalg RSA -keysize 2048 -validity 10000
fi

"$BUILD_TOOLS/apksigner" sign \
  --ks "$KEYSTORE" --ks-pass pass:android --key-pass pass:android \
  --out "$OUT/touch-executor.apk" "$OUT/touch-executor-unsigned.apk"
"$BUILD_TOOLS/apksigner" verify "$OUT/touch-executor.apk"
echo "$OUT/touch-executor.apk"
