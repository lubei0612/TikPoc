package com.tikpoc.bridge;

import java.util.Locale;

final class TikTokNotificationClassifier {
    private static final String MESSAGE_CATEGORY = "msg";

    private static final String[] FOLLOW_MARKERS = {
            "followed you",
            "new follower",
            "started following you",
            "关注了你",
            "新粉丝",
            "關注了你",
            "新粉絲",
            "追蹤了你",
            "新增追蹤者",
            "te siguió",
            "nuevo seguidor",
            "vous suit",
            "nouvel abonné",
            "folgt dir",
            "neuer follower",
            "ha iniziato a seguirti",
            "nuovo follower",
            "começou a seguir você",
            "novo seguidor",
            "новый подписчик",
            "あなたをフォロー",
            "新しいフォロワー",
            "회원님을 팔로우",
            "새 팔로워"
    };

    private static final String[] MESSAGE_MARKERS = {
            "sent you a message",
            "new message",
            "给你发了消息",
            "新消息",
            "傳送了訊息給你",
            "新訊息",
            "te envió un mensaje",
            "nouveau message",
            "hat dir eine nachricht gesendet",
            "ti ha inviato un messaggio",
            "enviou uma mensagem",
            "новое сообщение",
            "メッセージを送信しました",
            "메시지를 보냈습니다"
    };

    private TikTokNotificationClassifier() {}

    static String classify(String category, String title, String text) {
        String combined = ((title == null ? "" : title) + " "
                + (text == null ? "" : text)).toLowerCase(Locale.ROOT);
        if (containsAny(combined, FOLLOW_MARKERS)) return "new_follower";
        if (MESSAGE_CATEGORY.equals(category) || containsAny(combined, MESSAGE_MARKERS)) {
            return "dm_received";
        }
        return null;
    }

    private static boolean containsAny(String value, String[] markers) {
        for (String marker : markers) {
            if (value.contains(marker)) return true;
        }
        return false;
    }
}
