package com.yanyuliu.pockethub;
import static org.junit.Assert.*;
import java.net.URL;
import org.junit.Test;
public class ReleaseOriginTest {
 @Test public void accountPinOnlyAppliesToExactPublishedApkOrigin()throws Exception{
  String path="/v1/releases/apps/"+"a".repeat(64)+".apk";
  assertTrue(PinnedRelayTLS.isReleaseURL(new URL("https://150.158.176.23"+path)));
  assertFalse(PinnedRelayTLS.isReleaseURL(new URL("https://example.com"+path)));
  assertFalse(PinnedRelayTLS.isReleaseURL(new URL("http://150.158.176.23"+path)));
  assertFalse(PinnedRelayTLS.isReleaseURL(new URL("https://150.158.176.23:444"+path)));
  assertFalse(PinnedRelayTLS.isReleaseURL(new URL("https://150.158.176.23/v1/account/login")));
 }
}
