package com.tikpoc.touch;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Collections;

public final class LoopbackCommandServerTest {
    public static void main(String[] args) throws Exception {
        rejectsNonLoopbackBinding();
        rejectsInvalidFrameLengths();
        exchangesOneBoundedFrame();
        System.out.println("LoopbackCommandServerTest PASS");
    }

    private static void exchangesOneBoundedFrame() throws Exception {
        SemanticSnapshot.Node root = new SemanticSnapshot.Node(
                "root", "Frame", "", "", new SemanticSnapshot.Bounds(0, 0, 10, 10),
                true, false, true, false, Collections.<SemanticSnapshot.Node>emptyList());
        SemanticSnapshot snapshot = SemanticSnapshot.fromRoot(root, 1L, 1_000L);
        TouchCommandDispatcher dispatcher = new TouchCommandDispatcher(
                () -> snapshot,
                new TouchCommandDispatcher.Actuator() {
                    public boolean click(SemanticSnapshot.Node node) { return true; }
                    public boolean openProfile(String route) { return true; }
                },
                () -> 1_000L,
                new TouchCommandDispatcher.SurfaceSource() {
                    public String packageName() { return "com.zhiliaoapp.musically"; }
                    public String activityName() { return "MainActivity"; }
                });
        CommandGate gate = new CommandGate(() -> 1_000L);
        try (LoopbackCommandServer server = new LoopbackCommandServer(
                "127.0.0.1", 0, gate, dispatcher, () -> 1_000L)) {
            server.start();
            byte[] request = validHealthRequest().getBytes(StandardCharsets.UTF_8);
            try (Socket socket = new Socket("127.0.0.1", server.localPort());
                 DataOutputStream output = new DataOutputStream(socket.getOutputStream());
                 DataInputStream input = new DataInputStream(socket.getInputStream())) {
                output.writeInt(request.length);
                output.write(request);
                output.flush();
                int responseLength = input.readInt();
                LoopbackCommandServer.validateFrameLength(responseLength);
                byte[] response = new byte[responseLength];
                input.readFully(response);
                String encoded = new String(response, StandardCharsets.UTF_8);
                check(encoded.contains("\"status\":\"ok\""), "response status");
                check(encoded.contains("\"command_id\":\"socket-1\""), "response id");
            }
        }
    }

    private static String validHealthRequest() {
        return "{\"version\":1,\"command_id\":\"socket-1\","
                + "\"command\":\"health\",\"device_id\":\"device-1\","
                + "\"account_id\":\"account-1\",\"fence_token\":7,"
                + "\"assignment_id\":19,\"phase\":\"profile_opening\","
                + "\"deadline_elapsed_ms\":9000,\"arguments\":{}}";
    }

    private static void rejectsNonLoopbackBinding() {
        try {
            LoopbackCommandServer.validateBindAddress("0.0.0.0");
            throw new AssertionError("accepted public bind");
        } catch (IllegalArgumentException expected) {
            check(expected.getMessage().equals("loopback address required"), "bind error");
        }
    }

    private static void rejectsInvalidFrameLengths() {
        expectInvalid(0);
        expectInvalid(262_145);
        LoopbackCommandServer.validateFrameLength(262_144);
    }

    private static void expectInvalid(int length) {
        try {
            LoopbackCommandServer.validateFrameLength(length);
            throw new AssertionError("accepted invalid frame");
        } catch (IllegalArgumentException expected) {
            check(expected.getMessage().equals("invalid frame length"), "frame error");
        }
    }

    private static void check(boolean condition, String label) {
        if (!condition) throw new AssertionError(label);
    }
}
