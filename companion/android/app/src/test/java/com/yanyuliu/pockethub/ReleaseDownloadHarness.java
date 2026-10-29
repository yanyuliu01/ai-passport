package com.yanyuliu.pockethub;
import java.io.File;
/** Runs the production updater on a USB phone without changing app data or installing an APK. */
public class ReleaseDownloadHarness {
 public static void main(String[] args)throws Exception{
  File file=new File(args[3]);
  long resumed=new File(file.getPath()+".part").length();
  UpdateDownload.fetchExternal(args[0],args[1],Long.parseLong(args[2]),file,percent->{});
  System.out.println("phone-release-download-hash-verified bytes="+file.length()+" resumed="+resumed);
 }
}
