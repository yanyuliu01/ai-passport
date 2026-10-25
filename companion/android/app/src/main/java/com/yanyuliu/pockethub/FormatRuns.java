package com.yanyuliu.pockethub;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * The Gateway's formatting runs as plain text plus style ranges. No Markdown is parsed here: the
 * Runtime already resolved bold, italic, strike, code, headings, links and quotes. Kept free of
 * Android classes so the mapping is tested on the JVM; Markdown.projected applies the ranges.
 */
final class FormatRuns {
    enum Style { BOLD, ITALIC, STRIKE, CODE, CODE_BLOCK, HEADING, QUOTE, LINK }

    static final class Span {
        final Style style; final int start, end; final String value;
        Span(Style style, int start, int end, String value) { this.style = style; this.start = start; this.end = end; this.value = value; }
        @Override public String toString() { return style + "[" + start + "," + end + ")" + (value.isEmpty() ? "" : "=" + value); }
    }

    /** Only links a phone can open safely; the Runtime already drops others, this is a second fence. */
    private static final Pattern SAFE_LINK = Pattern.compile("(?i)^(https?://|mailto:).+");

    final String text;
    final List<Span> spans;

    private FormatRuns(String text, List<Span> spans) { this.text = text; this.spans = spans; }

    static FormatRuns of(List<Object> runs) {
        StringBuilder out = new StringBuilder();
        List<Span> spans = new ArrayList<>();
        for (Object value : runs) {
            Map<String, Object> run = Json.object(value);
            if (run == null) continue;
            String piece = Json.string(run, "text");
            int start = out.length();
            out.append(piece);
            int end = out.length();
            if (end == start) continue;
            if (Json.flag(run, "bold")) spans.add(new Span(Style.BOLD, start, end, ""));
            if (Json.flag(run, "italic")) spans.add(new Span(Style.ITALIC, start, end, ""));
            if (Json.flag(run, "strike")) spans.add(new Span(Style.STRIKE, start, end, ""));
            if (Json.flag(run, "quote")) spans.add(new Span(Style.QUOTE, start, end, ""));
            String code = Json.string(run, "code");
            if (code.equals("inline")) spans.add(new Span(Style.CODE, start, end, ""));
            else if (code.equals("block")) spans.add(new Span(Style.CODE_BLOCK, start, end, Json.string(run, "language")));
            Object heading = run.get("heading");
            if (heading instanceof Number && ((Number) heading).intValue() >= 1)
                spans.add(new Span(Style.HEADING, start, end, String.valueOf(Math.min(6, ((Number) heading).intValue()))));
            String href = Json.string(run, "href");
            if (SAFE_LINK.matcher(href).matches()) spans.add(new Span(Style.LINK, start, end, href));
        }
        return new FormatRuns(out.toString(), spans);
    }
}
