package com.yanyuliu.pockethub;

import static org.junit.Assert.assertEquals;
import org.junit.Test;

public class HomeMessageTest {
    @Test public void commonErrorsHaveChineseHomeText() {
        assertEquals("当前用量已到上限，请稍后再试。点开聊天可看原文。",
                HomeMessage.display("Rate limit exceeded"));
        assertEquals("这次没识别清楚，请再说一次。点开聊天可看原文。",
                HomeMessage.display("Speech not recognized"));
    }

    @Test public void untranslatedEnglishGetsChineseHint() {
        assertEquals("这条消息暂时没能转成中文，点开聊天查看原文。",
                HomeMessage.display("The assistant finished the requested task."));
        assertEquals("已经完成", HomeMessage.display("已经完成"));
    }
}
