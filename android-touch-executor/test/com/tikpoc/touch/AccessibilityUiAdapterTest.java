package com.tikpoc.touch;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

public final class AccessibilityUiAdapterTest {
    public static void main(String[] args) throws Exception {
        Adapter adapter = new Adapter();
        Map<String, Object> target = new LinkedHashMap<String, Object>();
        target.put("task_id", "19");
        target.put("target_id", "user-1");
        target.put("username", "target_user");
        target.put("profile_url", "https://www.tiktok.com/@target_user");

        adapter.openProfile(target);
        AutonomousTaskExecutor.Profile profile = adapter.observeProfile();

        check(adapter.last.command.equals("observe_profile"), "observe command");
        check(profile.username.equals("target_user"), "identity evidence");
        check(adapter.last.arguments.get("expected_username").equals("target_user"),
                "expected username bound");
        System.out.println("AccessibilityUiAdapterTest PASS");
    }

    private static final class Adapter extends AccessibilityUiAdapter {
        Protocol.Request last;

        Adapter() { super("device-1", "account-1", 7L, 1_000L, request -> {
            Map<String, Object> evidence = new LinkedHashMap<String, Object>();
            evidence.put("access_state", "available");
            evidence.put("username", "target_user");
            evidence.put("following", 10L);
            evidence.put("followers", 2L);
            evidence.put("video_count", 4L);
            evidence.put("post_handles", Collections.singletonList("video-1"));
            evidence.put("following_resource_id", "following");
            evidence.put("followers_resource_id", "followers");
            return Protocol.Response.success(request, 1L,
                    "com.zhiliaoapp.musically", "MainActivity", 1L, "digest", evidence);
        }); }

        @Override
        protected Protocol.Response invoke(Protocol.Request request) throws Exception {
            last = request;
            return super.invoke(request);
        }
    }

    private static void check(boolean condition, String label) {
        if (!condition) throw new AssertionError(label);
    }
}
