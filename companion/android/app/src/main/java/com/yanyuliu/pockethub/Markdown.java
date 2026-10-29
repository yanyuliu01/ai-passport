package com.yanyuliu.pockethub;

import android.graphics.Typeface;
import android.text.SpannableStringBuilder;
import android.text.Spanned;
import android.text.style.LeadingMarginSpan;
import android.text.style.StyleSpan;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 小鱼回复里的轻量格式：Runtime 只留下加粗 **…**、列表（“- ” 开头）、编号列表，以及她单独写一行
 * --- 表示另起一条消息。手机聊天里按这几样画出来（render）；设备小屏、首页那一句话、复制出去的
 * 文字用纯文字（plain）。
 */
final class Markdown {
    private static final Pattern BOLD = Pattern.compile("\\*\\*(.+?)\\*\\*");
    private static final Pattern BULLET = Pattern.compile("^\\s*[-*•·]\\s+");
    private static final Pattern NUMBERED = Pattern.compile("^\\s*(\\d+[.、)）])\\s*");
    private static final Pattern BREAK = Pattern.compile("(?m)^\\s*-{3,}\\s*$");

    private Markdown() { }

    /** Gateway formatting runs: no local Markdown parsing for projected records. */
    static CharSequence projected(Presentation view, int index) {
        java.util.List<Object> runs = view.runs(index);
        if (runs.isEmpty()) return view.blocks().get(index);
        FormatRuns format = FormatRuns.of(runs);
        SpannableStringBuilder out = new SpannableStringBuilder(format.text);
        for (FormatRuns.Span span : format.spans) for (Object style : spans(span))
            out.setSpan(style, span.start, span.end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        return out;
    }

    private static final int CODE_BACKGROUND = 0xffeef1f6, QUOTE_COLOR = 0xff5f6f84, QUOTE_BAR = 0xffc9d3e0;

    private static Object[] spans(FormatRuns.Span span) {
        switch (span.style) {
            case BOLD: return new Object[] {new StyleSpan(Typeface.BOLD)};
            case ITALIC: return new Object[] {new StyleSpan(Typeface.ITALIC)};
            case STRIKE: return new Object[] {new android.text.style.StrikethroughSpan()};
            case CODE: return new Object[] {new android.text.style.TypefaceSpan("monospace"), new android.text.style.BackgroundColorSpan(CODE_BACKGROUND)};
            case CODE_BLOCK: return new Object[] {new android.text.style.TypefaceSpan("monospace"), new android.text.style.BackgroundColorSpan(CODE_BACKGROUND),
                    new android.text.style.RelativeSizeSpan(0.92f)};
            case HEADING: return new Object[] {new android.text.style.RelativeSizeSpan(Integer.parseInt(span.value) <= 2 ? 1.18f : 1.06f)};
            case QUOTE: return new Object[] {new android.text.style.ForegroundColorSpan(QUOTE_COLOR), new android.text.style.QuoteSpan(QUOTE_BAR)};
            case LINK: return new Object[] {new android.text.style.URLSpan(span.value)};
            default: return new Object[0];
        }
    }

    /** 她要发的几条消息：按单独一行的 --- 分开，去掉空的。 */
    static List<String> messages(String text) {
        List<String> parts = new ArrayList<>();
        if (text == null) return parts;
        for (String part : BREAK.split(text)) if (!part.trim().isEmpty()) parts.add(part.trim());
        return parts;
    }

    /** 最后一个单独一行 --- 开始的位置；没有就是 0（还没有一条写完）。 */
    static int lastBreak(String text) {
        int at = 0;
        Matcher matcher = BREAK.matcher(text);
        while (matcher.find()) at = matcher.start();
        return at;
    }

    /** 纯文字：去掉加粗记号，列表换成 “· ”，几条消息之间空一行。 */
    static String plain(String text) {
        if (text == null || text.isEmpty()) return "";
        StringBuilder out = new StringBuilder();
        for (String part : messages(text)) {
            if (out.length() > 0) out.append("\n\n");
            String[] lines = part.split("\n", -1);
            for (int index = 0; index < lines.length; index++) {
                if (index > 0) out.append('\n');
                out.append(BULLET.matcher(lines[index]).replaceFirst("· "));
            }
        }
        return BOLD.matcher(out.toString()).replaceAll("$1");
    }

    /** 一条消息画出来：加粗变粗体，列表前面一个圆点、换行后对齐。indent 是列表文字缩进的像素。 */
    static CharSequence render(String text, int indent) {
        SpannableStringBuilder out = new SpannableStringBuilder();
        if (text == null) return out;
        String[] lines = text.split("\n", -1);
        for (int index = 0; index < lines.length; index++) {
            if (index > 0) out.append('\n');
            String line = lines[index];
            int start = out.length();
            Matcher bullet = BULLET.matcher(line), numbered = NUMBERED.matcher(line);
            boolean listed = false;
            if (bullet.find()) {
                out.append("•  "); line = line.substring(bullet.end()); listed = true;
            } else if (numbered.find()) {
                out.append(numbered.group(1)).append(' '); line = line.substring(numbered.end()); listed = true;
            }
            appendBold(out, line);
            if (listed) out.setSpan(new LeadingMarginSpan.Standard(0, indent), start, out.length(),
                    Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        }
        return out;
    }

    private static void appendBold(SpannableStringBuilder out, String line) {
        Matcher bold = BOLD.matcher(line);
        int at = 0;
        while (bold.find()) {
            out.append(line, at, bold.start());
            int start = out.length();
            out.append(bold.group(1));
            out.setSpan(new StyleSpan(Typeface.BOLD), start, out.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            at = bold.end();
        }
        out.append(line, at, line.length());
    }
}
