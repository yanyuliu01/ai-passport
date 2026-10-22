package com.yanyuliu.pockethub;

/** Keep small images and lossless screenshots intact; bound large photo transfers. */
final class ImagePreparation {
    static final int PHOTO_EDGE = 2560;
    static boolean resizePhoto(String mime, int bytes, int width, int height) {
        return "image/jpeg".equals(mime) && bytes > 1024 * 1024
                && Math.max(width, height) > PHOTO_EDGE;
    }

    static int sample(int width, int height, int edge) {
        int sample = 1;
        while (Math.max(width, height) / (sample * 2) >= edge) sample *= 2;
        return sample;
    }

    static int[] size(int width, int height, int edge) {
        double scale = Math.min(1d, (double) edge / Math.max(width, height));
        return new int[]{Math.max(1, (int) Math.round(width * scale)),
                Math.max(1, (int) Math.round(height * scale))};
    }
}
