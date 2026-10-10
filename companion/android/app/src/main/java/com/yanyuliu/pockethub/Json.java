package com.yanyuliu.pockethub;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 一个够用的 JSON 解析器，读 Runtime 返回的带嵌套的内容（卡、授权）。纯 Java，不依赖安卓，
 * 可以在电脑上直接测。
 *
 * 对象变成 Map，数组变成 List，字符串是 String，数字是 Double，true / false 是 Boolean，
 * null 是 null。格式不对时 parse 返回 null，不抛异常。
 */
final class Json {
    private static final int MAX_DEPTH = 32;

    private final String text;
    private int at;

    private Json(String text) {
        this.text = text;
    }

    private static final class Bad extends RuntimeException {
        Bad() {
            super(null, null, false, false);
        }
    }

    /** 解析一段 JSON；最外层必须是对象，否则返回 null。 */
    static Map<String, Object> parseObject(String text) {
        if (text == null) {
            return null;
        }
        Json json = new Json(text);
        try {
            Object value = json.value(0);
            json.space();
            if (json.at != text.length() || !(value instanceof Map)) {
                return null;
            }
            @SuppressWarnings("unchecked")
            Map<String, Object> object = (Map<String, Object>) value;
            return object;
        } catch (Bad | StringIndexOutOfBoundsException | NumberFormatException error) {
            return null;
        }
    }

    static String string(Map<String, Object> object, String key) {
        Object value = object == null ? null : object.get(key);
        return value instanceof String ? (String) value : "";
    }

    static double number(Map<String, Object> object, String key) {
        Object value = object == null ? null : object.get(key);
        return value instanceof Double ? (Double) value : 0;
    }

    static boolean flag(Map<String, Object> object, String key) {
        Object value = object == null ? null : object.get(key);
        return Boolean.TRUE.equals(value);
    }

    static List<Object> list(Map<String, Object> object, String key) {
        Object value = object == null ? null : object.get(key);
        if (value instanceof List) {
            @SuppressWarnings("unchecked")
            List<Object> list = (List<Object>) value;
            return list;
        }
        return new ArrayList<>();
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> object(Object value) {
        return value instanceof Map ? (Map<String, Object>) value : null;
    }

    private void space() {
        while (at < text.length() && Character.isWhitespace(text.charAt(at))) {
            at++;
        }
    }

    private Object value(int depth) {
        if (depth > MAX_DEPTH) {
            throw new Bad();
        }
        space();
        char first = text.charAt(at);
        if (first == '{') {
            at++;
            Map<String, Object> object = new LinkedHashMap<>();
            space();
            if (text.charAt(at) == '}') {
                at++;
                return object;
            }
            while (true) {
                space();
                String key = string();
                space();
                expect(':');
                object.put(key, value(depth + 1));
                space();
                char next = text.charAt(at++);
                if (next == '}') {
                    return object;
                }
                if (next != ',') {
                    throw new Bad();
                }
            }
        }
        if (first == '[') {
            at++;
            List<Object> list = new ArrayList<>();
            space();
            if (text.charAt(at) == ']') {
                at++;
                return list;
            }
            while (true) {
                list.add(value(depth + 1));
                space();
                char next = text.charAt(at++);
                if (next == ']') {
                    return list;
                }
                if (next != ',') {
                    throw new Bad();
                }
            }
        }
        if (first == '"') {
            return string();
        }
        if (text.startsWith("true", at)) {
            at += 4;
            return Boolean.TRUE;
        }
        if (text.startsWith("false", at)) {
            at += 5;
            return Boolean.FALSE;
        }
        if (text.startsWith("null", at)) {
            at += 4;
            return null;
        }
        int start = at;
        while (at < text.length() && "+-.eE0123456789".indexOf(text.charAt(at)) >= 0) {
            at++;
        }
        if (at == start) {
            throw new Bad();
        }
        return Double.parseDouble(text.substring(start, at));
    }

    private void expect(char wanted) {
        if (text.charAt(at++) != wanted) {
            throw new Bad();
        }
    }

    private String string() {
        expect('"');
        StringBuilder out = new StringBuilder();
        while (true) {
            char value = text.charAt(at++);
            if (value == '"') {
                return out.toString();
            }
            if (value != '\\') {
                out.append(value);
                continue;
            }
            char escaped = text.charAt(at++);
            switch (escaped) {
                case 'n':
                    out.append('\n');
                    break;
                case 't':
                    out.append('\t');
                    break;
                case 'r':
                    out.append('\r');
                    break;
                case 'b':
                    out.append('\b');
                    break;
                case 'f':
                    out.append('\f');
                    break;
                case 'u':
                    out.append((char) Integer.parseInt(text.substring(at, at + 4), 16));
                    at += 4;
                    break;
                default:
                    out.append(escaped);
            }
        }
    }
}
