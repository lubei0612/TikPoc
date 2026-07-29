package com.tikpoc.touch;

public final class BrowseGestureGeometryTest {
    public static void main(String[] args) {
        BrowseGestureGeometry.Swipe compact =
                BrowseGestureGeometry.forViewport(0, 0, 720, 1280);
        check(compact.startX >= 0 && compact.startX < 720, "compact start x");
        check(compact.endX >= 0 && compact.endX < 720, "compact end x");
        check(compact.startY >= 0 && compact.startY < 1280, "compact start y");
        check(compact.endY >= 0 && compact.endY < 1280, "compact end y");
        check(compact.startY > compact.endY, "compact upward swipe");

        BrowseGestureGeometry.Swipe inset =
                BrowseGestureGeometry.forViewport(10, 20, 1090, 1940);
        check(inset.startX >= 10 && inset.startX < 1090, "inset start x");
        check(inset.startY >= 20 && inset.startY < 1940, "inset start y");
        check(inset.endY >= 20 && inset.endY < 1940, "inset end y");
        check(inset.startY > inset.endY, "inset upward swipe");

        boolean rejected = false;
        try {
            BrowseGestureGeometry.forViewport(0, 0, 0, 1280);
        } catch (IllegalArgumentException expected) {
            rejected = true;
        }
        check(rejected, "empty viewport rejected");
        System.out.println("BrowseGestureGeometryTest passed");
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
