package com.yanyuliu.pockethub;

import java.util.Locale;

/** Short Chinese home-screen wording. The conversation and task keep the original text. */
final class HomeMessage {
    private HomeMessage() { }

    static String display(String original) {
        if (original == null || original.isEmpty()) return "";
        String value = original.toLowerCase(Locale.ROOT);
        if (value.contains("rate limit") || value.contains("too many requests")
                || value.contains("quota exceeded") || value.contains("usage limit"))
            return "当前用量已到上限，请稍后再试。点开聊天可看原文。";
        if (value.contains("unauthorized") || value.contains("authentication")
                || value.contains("invalid api key") || value.contains("invalid token"))
            return "连接凭据未通过验证，请检查电脑端配置。点开聊天可看原文。";
        if (value.contains("timeout") || value.contains("timed out"))
            return "等待电脑回复超时，请稍后重试。点开聊天可看原文。";
        if (value.contains("connection refused") || value.contains("network unreachable")
                || value.contains("failed to connect"))
            return "暂时连不上电脑，请检查连接。点开聊天可看原文。";
        if (value.contains("model") && (value.contains("not available")
                || value.contains("not found") || value.contains("unsupported")))
            return "当前模型暂不可用，请检查电脑端配置。点开聊天可看原文。";
        if (value.contains("unrecognized") || value.contains("could not recognize")
                || value.contains("speech not recognized"))
            return "这次没识别清楚，请再说一次。点开聊天可看原文。";
        int han = 0, latin = 0;
        for (int index = 0; index < original.length(); index++) {
            char c = original.charAt(index);
            if (Character.UnicodeScript.of(c) == Character.UnicodeScript.HAN) han++;
            else if (c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z') latin++;
        }
        if (han == 0 && latin >= 12)
            return "这条消息暂时没能转成中文，点开聊天查看原文。";
        return original;
    }
}
