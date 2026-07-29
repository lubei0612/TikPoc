package com.tikpoc.touch;

public final class BrowseGestureGeometry {
    public static final class Swipe {
        public final int startX;
        public final int startY;
        public final int endX;
        public final int endY;

        private Swipe(int startX, int startY, int endX, int endY) {
            this.startX = startX;
            this.startY = startY;
            this.endX = endX;
            this.endY = endY;
        }
    }

    private BrowseGestureGeometry() {}

    public static Swipe forViewport(int left, int top, int right, int bottom) {
        int width = right - left;
        int height = bottom - top;
        if (width <= 0 || height <= 0) {
            throw new IllegalArgumentException("viewport must have positive area");
        }
        int x = left + width / 2;
        int startY = top + (int) ((long) height * 3L / 4L);
        int endY = top + (int) ((long) height * 7L / 20L);
        return new Swipe(
                clamp(x, left, right - 1),
                clamp(startY, top, bottom - 1),
                clamp(x, left, right - 1),
                clamp(endY, top, bottom - 1));
    }

    private static int clamp(int value, int minimum, int maximum) {
        return Math.max(minimum, Math.min(value, maximum));
    }
}
