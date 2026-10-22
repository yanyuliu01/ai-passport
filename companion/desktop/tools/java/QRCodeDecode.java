import java.awt.image.BufferedImage;
import javax.imageio.ImageIO;
import java.nio.file.*;
import com.google.zxing.*;
import com.google.zxing.common.HybridBinarizer;
public class QRCodeDecode {
 public static void main(String[] args)throws Exception{
  BufferedImage image=ImageIO.read(Path.of(args[0]).toFile());int width=image.getWidth(),height=image.getHeight();
  RGBLuminanceSource source=new RGBLuminanceSource(width,height,image.getRGB(0,0,width,height,null,0,width));
  String link=new MultiFormatReader().decode(new BinaryBitmap(new HybridBinarizer(source))).getText();
  if(!link.equals(Files.readString(Path.of(args[1]))))throw new Exception("QR payload differs from invitation");
  System.out.println("Desktop invitation decoded by Android ZXing core: PASS");
 }
}
