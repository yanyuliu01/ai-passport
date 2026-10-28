package com.yanyuliu.pockethub;

/**
 * Keep small images and lossless screenshots intact; turn everything else into a JPEG that the
 * encrypted relay and the Runtime both accept.
 */
final class ImagePreparation {
    static final int PHOTO_EDGE = 2560;
    /** What the phone will read from the gallery before compressing; 50 MP camera shots fit. */
    static final int SOURCE_BYTES = 40 * 1024 * 1024;
    static final long SOURCE_PIXELS = 200_000_000L;
    /**
     * Upper bound for one sent image. The desktop relay (peers.ts allowedRPC) takes about 2.8 MB,
     * but every relayed request must finish within the desktop's 30 s RPC deadline, and larger
     * bodies miss it on mobile networks. Like WeChat's default, send a compressed copy under 1 MB.
     */
    static final int RELAY_BYTES = 1_000_000;
    /** Long edge of a re-encoded photo, as in WeChat's default (non-original) send. */
    static final int SEND_EDGE = 1920;
    /** Runtime MAX_IMAGE_PIXELS is 16 MP; leave room so a re-encoded long screenshot stays sharp but small. */
    static final long SEND_PIXELS = 8_000_000L;
    static final long RUNTIME_PIXELS = 16_000_000L;

    static boolean resizePhoto(String mime, int bytes, int width, int height) {
        return "image/jpeg".equals(mime) && bytes > 1024 * 1024
                && Math.max(width, height) > PHOTO_EDGE;
    }

    /** Both the relay and the Runtime accept these; HEIC, GIF and BMP are re-encoded. */
    static boolean sendable(String mime) {
        return "image/jpeg".equals(mime) || "image/png".equals(mime) || "image/webp".equals(mime);
    }

    static boolean keepOriginal(String mime, int bytes, int width, int height) {
        return sendable(mime) && bytes <= RELAY_BYTES && (long) width * height <= RUNTIME_PIXELS
                && !resizePhoto(mime, bytes, width, height);
    }

    /**
     * Size of the re-encoded copy. Photos keep a 1920 long edge; screenshots (taller than 2:1)
     * are bounded only by pixel count, so their text stays readable instead of shrinking to a strip.
     */
    static int[] fit(int width, int height) {
        int longEdge = Math.max(width, height), shortEdge = Math.max(1, Math.min(width, height));
        double scale = Math.min(1d, Math.sqrt((double) SEND_PIXELS / ((double) width * height)));
        if (longEdge <= shortEdge * 2) scale = Math.min(scale, (double) SEND_EDGE / longEdge);
        return new int[]{Math.max(1, (int) Math.round(width * scale)), Math.max(1, (int) Math.round(height * scale))};
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
