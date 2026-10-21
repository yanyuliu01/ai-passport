package com.yanyuliu.pockethub;
/** Fixed v1 preview format: 64 x 64, little-endian RGB565; no Android APIs. */
final class ImagePreview {
    static final int SIDE = 64, BYTES = SIDE * SIDE * 2;
    static byte[] rgb565(int[] colors) {
        if (colors.length != SIDE * SIDE) throw new IllegalArgumentException("Wrong preview dimensions");
        byte[] bytes = new byte[BYTES];
        for (int i = 0; i < colors.length; i++) {
            int color = colors[i];
            int value = ((color >> 19) & 31) << 11 | ((color >> 10) & 63) << 5 | ((color >> 3) & 31);
            bytes[i * 2] = (byte)value; bytes[i * 2 + 1] = (byte)(value >> 8);
        }
        return bytes;
    }
}
