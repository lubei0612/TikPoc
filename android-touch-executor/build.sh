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
mkdir -p "$OUT/classes" "$OUT/test-classes"

"$JDK/bin/javac" \
  -source 8 -target 8 \
  -classpath "$PLATFORM" \
  -d "$OUT/classes" \
  "$ROOT/src/com/tikpoc/touch/Protocol.java" \
  "$ROOT/src/com/tikpoc/touch/SemanticSnapshot.java" \
  "$ROOT/src/com/tikpoc/touch/TikTokSemantics.java" \
  "$ROOT/src/com/tikpoc/touch/CommandGate.java"

"$JDK/bin/javac" \
  -source 8 -target 8 \
  -classpath "$PLATFORM:$OUT/classes" \
  -d "$OUT/test-classes" \
  "$ROOT/test/com/tikpoc/touch/ProtocolTest.java" \
  "$ROOT/test/com/tikpoc/touch/TikTokSemanticsTest.java" \
  "$ROOT/test/com/tikpoc/touch/CommandGateTest.java"

"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.ProtocolTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.TikTokSemanticsTest
"$JDK/bin/java" -cp "$PLATFORM:$OUT/classes:$OUT/test-classes" \
  com.tikpoc.touch.CommandGateTest
