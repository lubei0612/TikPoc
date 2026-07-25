package com.tikpoc.touch;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

public final class LoopbackCommandServer implements AutoCloseable {
    private final ServerSocket server;
    private final CommandGate gate;
    private final TouchCommandDispatcher dispatcher;
    private final TouchCommandDispatcher.Clock clock;
    private volatile boolean running;
    private Thread thread;

    public LoopbackCommandServer(
            String bindAddress, int port, CommandGate gate,
            TouchCommandDispatcher dispatcher, TouchCommandDispatcher.Clock clock)
            throws Exception {
        validateBindAddress(bindAddress);
        this.server = new ServerSocket(port, 4, InetAddress.getByName(bindAddress));
        this.server.setSoTimeout(1_000);
        this.gate = gate;
        this.dispatcher = dispatcher;
        this.clock = clock;
    }

    public static void validateBindAddress(String address) {
        try {
            if (!InetAddress.getByName(address).isLoopbackAddress()) {
                throw new IllegalArgumentException("loopback address required");
            }
        } catch (java.net.UnknownHostException error) {
            throw new IllegalArgumentException("loopback address required");
        }
    }

    public static void validateFrameLength(int length) {
        if (length <= 0 || length > Protocol.MAX_REQUEST_BYTES) {
            throw new IllegalArgumentException("invalid frame length");
        }
    }

    public synchronized void start() {
        if (running) return;
        running = true;
        thread = new Thread(this::serve, "tikpoc-touch-loopback");
        thread.start();
    }

    public int localPort() {
        return server.getLocalPort();
    }

    private void serve() {
        while (running) {
            try {
                Socket socket = server.accept();
                socket.setSoTimeout(10_000);
                new Thread(() -> handle(socket), "tikpoc-touch-command").start();
            } catch (java.net.SocketTimeoutException expected) {
                // Recheck the running flag.
            } catch (Exception error) {
                if (running) android.util.Log.w("TikPocTouch", "loopback accept failed");
            }
        }
    }

    private void handle(Socket socket) {
        try (Socket owned = socket;
             DataInputStream input = new DataInputStream(owned.getInputStream());
             DataOutputStream output = new DataOutputStream(owned.getOutputStream())) {
            int length = input.readInt();
            validateFrameLength(length);
            byte[] payload = new byte[length];
            input.readFully(payload);
            Protocol.Request request = Protocol.parseRequest(
                    new String(payload, StandardCharsets.UTF_8), clock.elapsedRealtimeMs());
            Protocol.Response response = gate.execute(request, () -> dispatcher.dispatch(request));
            byte[] encoded = Protocol.encodeResponse(response).getBytes(StandardCharsets.UTF_8);
            output.writeInt(encoded.length);
            output.write(encoded);
            output.flush();
        } catch (Exception error) {
            android.util.Log.w("TikPocTouch", "command rejected");
        }
    }

    @Override
    public synchronized void close() throws Exception {
        running = false;
        server.close();
        if (thread != null) thread.join(2_000L);
    }
}
