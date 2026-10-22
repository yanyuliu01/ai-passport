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
}
