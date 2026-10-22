package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.assertNotNull;

public class ScannerDependenciesTest {
    @Test public void cameraPermissionAndScannerContractsHaveTheirRuntimeClasses() throws Exception {
        ClassLoader loader = ScannerDependenciesTest.class.getClassLoader();
        for (String name : new String[] {
                "com.journeyapps.barcodescanner.CaptureActivity",
                "com.journeyapps.barcodescanner.CaptureManager",
                "androidx.core.content.ContextCompat",
                "androidx.core.app.ActivityCompat",
                "androidx.fragment.app.Fragment",
                "androidx.activity.result.contract.ActivityResultContract"}) {
            assertNotNull(name, Class.forName(name, false, loader));
        }
    }
}
