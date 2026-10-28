package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;

public class ImagePreparationTest {
    @Test public void screenshotsTransparencyAnimationAndSmallPhotosKeepOriginalBytes() {
        for (String mime : new String[]{"image/png", "image/webp", "image/gif"})
            assertFalse(ImagePreparation.resizePhoto(mime, 6000000, 3000, 4000));
        assertFalse(ImagePreparation.resizePhoto("image/jpeg", 900000, 4000, 3000));
        assertFalse(ImagePreparation.resizePhoto("image/jpeg", 3000000, 2560, 1920));
        assertTrue(ImagePreparation.resizePhoto("image/jpeg", 3000000, 4000, 3000));
    }

    @Test public void photoAndPreviewKeepAspectRatioAndNeverUpscale() {
        assertArrayEquals(new int[]{2560, 1920}, ImagePreparation.size(4000, 3000, 2560));
        assertArrayEquals(new int[]{1920, 2560}, ImagePreparation.size(3000, 4000, 2560));
        assertArrayEquals(new int[]{240, 400}, ImagePreparation.size(2400, 4000, 400));
        assertArrayEquals(new int[]{100, 50}, ImagePreparation.size(100, 50, 400));
        assertEquals(8, ImagePreparation.sample(4000, 3000, 400));
        assertEquals(1, ImagePreparation.sample(4000, 3000, 2560));
        assertArrayEquals(new int[]{1, 400}, ImagePreparation.size(1, 16000, 400));
    }

    @Test public void onlyRelaySafeImagesKeepTheirOriginalBytes() {
        assertTrue(ImagePreparation.keepOriginal("image/png", 400000, 1200, 2670));
        assertTrue(ImagePreparation.keepOriginal("image/jpeg", 900000, 2560, 1920));
        // HEIC and GIF are refused by the desktop relay; large screenshots exceed the 1 MB send bound.
        assertFalse(ImagePreparation.keepOriginal("image/heif", 900000, 4000, 3000));
        assertFalse(ImagePreparation.keepOriginal("image/gif", 200000, 400, 400));
        assertFalse(ImagePreparation.keepOriginal("image/png", 2000000, 1200, 2670));
        assertFalse(ImagePreparation.keepOriginal("image/jpeg", 2000000, 8160, 6120));
        assertFalse(ImagePreparation.keepOriginal("image/png", 2000000, 1080, 20000));
    }

    @Test public void reencodedPhotosKeepAnEdgeAndTallScreenshotsKeepReadableWidth() {
        assertArrayEquals(new int[]{1920, 1440}, ImagePreparation.fit(4000, 3000));
        assertArrayEquals(new int[]{1280, 1920}, ImagePreparation.fit(8160, 12240));
        int[] tall = ImagePreparation.fit(1080, 20000);
        assertTrue(tall[0] >= 600 && (long) tall[0] * tall[1] <= ImagePreparation.SEND_PIXELS + 2000);
        assertArrayEquals(new int[]{1200, 2670}, ImagePreparation.fit(1200, 2670));
    }
}
