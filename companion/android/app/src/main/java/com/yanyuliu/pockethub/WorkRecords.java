package com.yanyuliu.pockethub;

import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.Locale;

/** A stale observation is retained history, never evidence of work still running. */
final class WorkRecords {
    enum Group { WAITING, RUNNING, UNCERTAIN, HISTORY }
    private WorkRecords() { }

    static Group group(Card card, boolean connected, double now) {
        boolean expired = card.presentation == null && card.monitoringOnly && card.active()
                && (card.observedAt <= 0 || now - card.observedAt > 300);
        if (card.state.equals("unknown") || card.stale || expired
                || (!connected && card.outstanding())) return Group.UNCERTAIN;
        if (card.presentation != null && card.presentation.supported) {
            switch (card.presentation.value("group")) {
                case "waiting": return Group.WAITING;
                case "running": return Group.RUNNING;
                case "uncertain": return Group.UNCERTAIN;
                case "history": return Group.HISTORY;
            }
        }
        if (card.state.equals("waiting")) return Group.WAITING;
        return card.active() ? Group.RUNNING : Group.HISTORY;
    }

    /** The three groups 在做的事 shows; an unconfirmed task has not ended, so it stays with the running ones. */
    enum Section { WAITING, RUNNING, ENDED }

    /** 在做的事 lists only work handed to a helper; observed computer sessions are reference material. */
    static boolean listed(Card card) { return AppPresentation.dispatched(card); }

    static Section section(Card card, boolean connected, double now) {
        switch (group(card, connected, now)) {
            case WAITING: return Section.WAITING;
            case HISTORY: return Section.ENDED;
            default: return Section.RUNNING;
        }
    }

    /** The latest progress sentence: the Gateway's marker-free text when projected, else the raw last line. */
    static String progress(Card card) {
        if (card.presentation != null && card.presentation.supported && !card.presentation.value("progress").isEmpty())
            return card.presentation.value("progress");
        return card.progress.isEmpty() ? "" : card.progress.get(card.progress.size() - 1);
    }

    /** 补充要求走到哪一步，各自来自对应的事实；和电脑客户端用同一套说法。 */
    static String relayLabel(String state) {
        switch (state == null ? "" : state) {
            case "": return "";
            case "supplement_received": case "received": return "已收到补充要求";
            case "relay_queued": case "queued": return "补充要求等本轮结束后转交";
            case "relayed": return "补充要求已转交原会话";
            case "executing": return "正在执行补充要求";
            case "interrupted": return "小幽重启过，补充要求还留着，等你确认后再转交";
            default: return "补充要求状态：" + state;
        }
    }

    static String title(Card card) {
        if (!card.title.isEmpty() && !card.titleSource.equals("fallback")) return card.title;
        return card.monitoringOnly ? "未命名 " + AppPresentation.name(AppPresentation.owner(card)) + " 会话" : "未命名工作";
    }

    static String source(Card card, String runtimeName) {
        String host = !card.originHost.isEmpty() ? card.originHost : runtimeName;
        if (host == null || host.isEmpty()) host = "来源未标明";
        String result = host + " · " + AppPresentation.name(AppPresentation.owner(card));
        String project = card.displayProject.isEmpty() ? card.project : card.displayProject;
        if (project.isEmpty() && !card.workerCwd.isEmpty()) {
            String path = card.workerCwd.replace('\\', '/').replaceAll("/+$", "");
            project = path.substring(path.lastIndexOf('/') + 1);
        }
        if (!project.isEmpty()) result += (project.equals("workdir") ? " · 目录：" : " · ") + project;
        return result;
    }

    static String status(Card card, boolean connected, double now, ZoneId zone) {
        Group group = group(card, connected, now);
        if (group == Group.UNCERTAIN) {
            String label = connected ? "状态待确认" : "连不上，状态待确认";
            double at = card.monitoringOnly ? card.observedAt : card.lastMeaningfulAt;
            return label + (valid(at) ? " · 上次记录 " + time(at, now, zone) : " · 尚无活动记录");
        }
        if (group == Group.HISTORY) {
            String label = card.turnEnded || (card.monitoringOnly && card.state.equals("done")) ? "本轮结束"
                    : card.state.equals("failed") ? "未成功" : card.state.equals("cancelled") ? "已停止" : "已完成";
            double at = valid(card.finishedAt) ? card.finishedAt : card.observedAt;
            return label + (valid(at) ? " · " + time(at, now, zone) : "");
        }
        if (group == Group.WAITING) return card.monitoringOnly ? "等待电脑端确认" : "等你确认";
        if (card.queued) return "排队，等待开始";
        if (card.monitoringOnly) return "正在执行 · 上次活动 " + time(card.observedAt, now, zone);
        if (valid(card.lastMeaningfulAt)) {
            long seconds = Math.max(0, (long) (now - card.lastMeaningfulAt));
            return seconds < 60 ? "刚有进展" : seconds < 3600 ? seconds / 60 + " 分钟无新进展"
                    : seconds / 3600 + " 小时无新进展";
        }
        return valid(card.startedAt) ? "开始于 " + time(card.startedAt, now, zone) + " · 等待进展" : "等待第一条进展";
    }

    private static boolean valid(double value) { return Double.isFinite(value) && value > 0; }
    private static String time(double at, double now, ZoneId zone) {
        java.time.ZonedDateTime stamp = Instant.ofEpochSecond((long) at).atZone(zone);
        boolean today = stamp.toLocalDate().equals(Instant.ofEpochSecond((long) now).atZone(zone).toLocalDate());
        return DateTimeFormatter.ofPattern(today ? "HH:mm" : "MM-dd HH:mm", Locale.ROOT).format(stamp);
    }
}
