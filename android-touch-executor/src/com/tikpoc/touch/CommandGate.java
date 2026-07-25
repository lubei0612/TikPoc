package com.tikpoc.touch;

import java.nio.charset.StandardCharsets;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Map;

public final class CommandGate {
    private static final int MAX_COMPLETED_ENTRIES = 32;
    private static final int MAX_COMPLETED_BYTES = 262_144;
    private static final long COMPLETED_TTL_MS = 60_000L;

    public interface Clock {
        long elapsedRealtimeMs();
    }

    public interface Operation {
        Protocol.Response run() throws Exception;
    }

    private static final class Completed {
        private final Protocol.Response response;
        private final int encodedBytes;
        private final long completedAtMs;

        private Completed(Protocol.Response response, int encodedBytes, long completedAtMs) {
            this.response = response;
            this.encodedBytes = encodedBytes;
            this.completedAtMs = completedAtMs;
        }
    }

    private static final class InFlight {
        private final String commandId;
        private Protocol.Response response;
        private Exception failure;
        private boolean done;

        private InFlight(String commandId) {
            this.commandId = commandId;
        }
    }

    private final Clock clock;
    private final LinkedHashMap<String, Completed> completed =
            new LinkedHashMap<String, Completed>();
    private InFlight inFlight;
    private int completedBytes;

    public CommandGate(Clock clock) {
        if (clock == null) throw new IllegalArgumentException("clock is required");
        this.clock = clock;
    }

    public Protocol.Response execute(Protocol.Request request, Operation operation)
            throws Exception {
        InFlight admitted;
        synchronized (this) {
            evictExpired();
            Completed cached = completed.get(request.commandId);
            if (cached != null) return cached.response;
            if (inFlight != null) {
                if (!inFlight.commandId.equals(request.commandId)) {
                    return Protocol.Response.error(request, "busy", "another command is active");
                }
                return await(inFlight);
            }
            admitted = new InFlight(request.commandId);
            inFlight = admitted;
        }

        Protocol.Response response;
        try {
            response = operation.run();
            int encodedBytes = Protocol.encodeResponse(response)
                    .getBytes(StandardCharsets.UTF_8).length;
            synchronized (this) {
                admitted.response = response;
                admitted.done = true;
                cache(request.commandId, response, encodedBytes);
                inFlight = null;
                notifyAll();
            }
            return response;
        } catch (Exception error) {
            synchronized (this) {
                admitted.failure = error;
                admitted.done = true;
                inFlight = null;
                notifyAll();
            }
            throw error;
        } catch (Error error) {
            synchronized (this) {
                admitted.failure = new RuntimeException("command failed", error);
                admitted.done = true;
                inFlight = null;
                notifyAll();
            }
            throw error;
        }
    }

    public synchronized int completedCount() {
        evictExpired();
        return completed.size();
    }

    public synchronized int completedBytes() {
        evictExpired();
        return completedBytes;
    }

    private Protocol.Response await(InFlight command) throws Exception {
        while (!command.done) wait();
        if (command.failure != null) throw command.failure;
        return command.response;
    }

    private void cache(String commandId, Protocol.Response response, int encodedBytes) {
        Completed prior = completed.remove(commandId);
        if (prior != null) completedBytes -= prior.encodedBytes;
        completed.put(commandId, new Completed(response, encodedBytes, clock.elapsedRealtimeMs()));
        completedBytes += encodedBytes;
        Iterator<Map.Entry<String, Completed>> entries = completed.entrySet().iterator();
        while ((completed.size() > MAX_COMPLETED_ENTRIES
                || completedBytes > MAX_COMPLETED_BYTES) && entries.hasNext()) {
            Completed removed = entries.next().getValue();
            completedBytes -= removed.encodedBytes;
            entries.remove();
        }
    }

    private void evictExpired() {
        long nowMs = clock.elapsedRealtimeMs();
        Iterator<Map.Entry<String, Completed>> entries = completed.entrySet().iterator();
        while (entries.hasNext()) {
            Completed entry = entries.next().getValue();
            if (nowMs - entry.completedAtMs > COMPLETED_TTL_MS) {
                completedBytes -= entry.encodedBytes;
                entries.remove();
            }
        }
    }
}
