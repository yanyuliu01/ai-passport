import java.awt.AlphaComposite;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.io.File;
import javax.imageio.ImageIO;

/** Run with JDK 17: java -Djava.awt.headless=true companion/android/tools/PreparePortraits.java <repo> [role].
 * Retains source art; explicitly crops reviewed cells and centers each on a transparent 256px square.
 */
class PreparePortraits {
    public static void main(String[] args) throws Exception {
        File root = new File(args.length == 0 ? "." : args[0]);
        File source = new File(root, "assets/images/ai-companions");
        File output = new File(root, "companion/android/app/src/main/res/drawable-nodpi");
        if (!output.isDirectory() && !output.mkdirs()) throw new IllegalStateException("Cannot create output directory");
        String[] roles = {"ds", "codex", "claude"};
        String[] atlases = {"avatar-whale-v1.png", "avatar-chatgpt-pixel-v1.png", "avatar-claude-v2.png"};
        String[] moods = {"idle", "happy", "think", "focus"};
        int[][][] bounds = {
            {{16,8,346,291},{363,8,690,292},{1035,8,1330,292},{16,301,344,586}},
            {{4,2,359,272},{366,2,720,272},{1088,2,1447,272},{3,275,359,542}},
            {{4,10,330,308},{334,10,657,304},{988,10,1311,308},{3,306,329,606}}
        };
        for (int role = 0; role < roles.length; role++) {
            if (args.length > 1 && !roles[role].equals(args[1])) continue;
            BufferedImage atlas = ImageIO.read(new File(source, atlases[role]));
            if (atlas == null) throw new IllegalArgumentException("Unreadable atlas: " + atlases[role]);
            for (int mood = 0; mood < moods.length; mood++) {
                int[] b = bounds[role][mood];
                int width = b[2] - b[0], height = b[3] - b[1];
                BufferedImage crop = atlas.getSubimage(b[0], b[1], width, height);
                BufferedImage portrait = new BufferedImage(256, 256, BufferedImage.TYPE_INT_ARGB);
                Graphics2D g = portrait.createGraphics();
                g.setComposite(AlphaComposite.Src);
                g.setRenderingHint(RenderingHints.KEY_INTERPOLATION, role == 0 || role == 1
                        ? RenderingHints.VALUE_INTERPOLATION_NEAREST_NEIGHBOR : RenderingHints.VALUE_INTERPOLATION_BICUBIC);
                double scale = 240.0 / Math.max(width, height);
                int w = (int)Math.round(width * scale), h = (int)Math.round(height * scale);
                // Busts stand on the bottom edge; Codex's pixel head is wider than tall, so it is
                // centered instead of sinking toward the bottom of round frames.
                int top = roles[role].equals("codex") ? (256 - h) / 2 : 248 - h;
                g.drawImage(crop, (256 - w) / 2, top, w, h, null);
                g.dispose();
                File file = new File(output, "portrait_" + roles[role] + "_" + moods[mood] + ".png");
                if (!ImageIO.write(portrait, "png", file)) throw new IllegalStateException("No PNG encoder");
                System.out.println(file.getName());
            }
        }
    }
}
