package com.tikpoc.touch;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class Protocol {
    public static final int VERSION = 1;
    public static final String HELPER_VERSION = "1.0.0";
    public static final int MAX_REQUEST_BYTES = 262_144;
    private static final int MAX_JSON_DEPTH = 12;
    private static final List<String> COMMANDS = Collections.unmodifiableList(Arrays.asList(
            "health", "open_profile", "open_profile_search", "observe_profile", "open_video",
            "observe_action", "apply_action", "browse_home", "diagnostics"));
    private static final List<String> PHASES = Collections.unmodifiableList(Arrays.asList(
            "pending", "profile_opening", "identity_confirmed", "waiting_snapshot",
            "video_opening", "video_confirmed", "quota_reserved", "action_executing",
            "action_reconciling", "session_pacing", "completed", "deferred", "skipped"));

    private Protocol() {}

    public static final class ProtocolException extends Exception {
        public final String code;

        public ProtocolException(String code) {
            super(code);
            this.code = code;
        }
    }

    public static final class Request {
        public final int version;
        public final String commandId;
        public final String command;
        public final String deviceId;
        public final String accountId;
        public final long fenceToken;
        public final long assignmentId;
        public final String phase;
        public final long deadlineElapsedMs;
        public final Map<String, Object> arguments;

        private Request(Map<String, Object> values) throws ProtocolException {
            version = requiredInt(values, "version");
            commandId = requiredString(values, "command_id");
            command = requiredString(values, "command");
            deviceId = requiredString(values, "device_id");
            accountId = requiredString(values, "account_id");
            fenceToken = requiredPositiveLong(values, "fence_token");
            assignmentId = requiredPositiveLong(values, "assignment_id");
            phase = requiredString(values, "phase");
            deadlineElapsedMs = requiredPositiveLong(values, "deadline_elapsed_ms");
            arguments = requiredObject(values, "arguments");
        }
    }

    public static final class Response {
        public final Map<String, Object> values;

        private Response(Map<String, Object> values) {
            this.values = Collections.unmodifiableMap(values);
        }

        public static Response success(
                Request request,
                long elapsedMs,
                String packageName,
                String activityName,
                long eventSequence,
                String evidenceDigest,
                Map<String, Object> evidence) {
            Map<String, Object> values = base(request);
            values.put("status", "ok");
            values.put("elapsed_ms", elapsedMs);
            values.put("package_name", packageName);
            values.put("activity_name", activityName);
            values.put("event_sequence", eventSequence);
            values.put("evidence_digest", evidenceDigest);
            values.put("evidence", new LinkedHashMap<String, Object>(evidence));
            return new Response(values);
        }

        public static Response error(Request request, String code, String message) {
            Map<String, Object> values = base(request);
            values.put("status", "error");
            values.put("error", errorValues(code, message));
            return new Response(values);
        }

        public static Response uncertain(
                Request request, long elapsedMs, String packageName, String activityName,
                long eventSequence, String evidenceDigest, Map<String, Object> evidence) {
            Map<String, Object> values = base(request);
            values.put("status", "uncertain");
            values.put("elapsed_ms", elapsedMs);
            values.put("package_name", packageName);
            values.put("activity_name", activityName);
            values.put("event_sequence", eventSequence);
            values.put("evidence_digest", evidenceDigest);
            values.put("evidence", new LinkedHashMap<String, Object>(evidence));
            return new Response(values);
        }

        public Response withPerformance(long treeAgeMs, long eventWaitMs) {
            Map<String, Object> measured = new LinkedHashMap<String, Object>(values);
            measured.put("tree_age_ms", Math.max(0L, treeAgeMs));
            measured.put("event_wait_ms", Math.max(0L, eventWaitMs));
            return new Response(measured);
        }

        private static Map<String, Object> base(Request request) {
            Map<String, Object> values = new LinkedHashMap<String, Object>();
            values.put("version", VERSION);
            values.put("helper_version", HELPER_VERSION);
            values.put("command_id", request.commandId);
            values.put("device_id", request.deviceId);
            values.put("account_id", request.accountId);
            values.put("fence_token", request.fenceToken);
            values.put("assignment_id", request.assignmentId);
            values.put("phase", request.phase);
            return values;
        }

        private static Map<String, Object> errorValues(String code, String message) {
            Map<String, Object> error = new LinkedHashMap<String, Object>();
            error.put("code", code);
            error.put("message", message);
            return error;
        }
    }

    public static Request parseRequest(String encoded, long nowElapsedMs)
            throws ProtocolException {
        if (encoded == null
                || encoded.getBytes(StandardCharsets.UTF_8).length > MAX_REQUEST_BYTES) {
            throw new ProtocolException("request_too_large");
        }
        Object parsed = new JsonReader(encoded).read();
        if (!(parsed instanceof Map)) {
            throw new ProtocolException("request_not_object");
        }
        @SuppressWarnings("unchecked")
        Map<String, Object> values = (Map<String, Object>) parsed;
        Request request = new Request(values);
        if (request.version != VERSION) {
            throw new ProtocolException("unsupported_version");
        }
        if (!COMMANDS.contains(request.command)) {
            throw new ProtocolException("unsupported_command");
        }
        if (!PHASES.contains(request.phase)) {
            throw new ProtocolException("unsupported_phase");
        }
        if (request.deadlineElapsedMs <= nowElapsedMs) {
            throw new ProtocolException("deadline_expired");
        }
        return request;
    }

    public static String encodeResponse(Response response) throws ProtocolException {
        StringBuilder encoded = new StringBuilder();
        writeJson(response.values, encoded, 0);
        if (encoded.toString().getBytes(StandardCharsets.UTF_8).length > MAX_REQUEST_BYTES) {
            throw new ProtocolException("response_too_large");
        }
        return encoded.toString();
    }

    public static Map<String, Object> decodeObject(String encoded) throws ProtocolException {
        Object parsed = new JsonReader(encoded).read();
        if (!(parsed instanceof Map)) throw new ProtocolException("response_not_object");
        @SuppressWarnings("unchecked")
        Map<String, Object> values = (Map<String, Object>) parsed;
        return values;
    }

    public static String encodeObject(Map<String, Object> values) throws ProtocolException {
        StringBuilder encoded = new StringBuilder();
        writeJson(values, encoded, 0);
        if (encoded.toString().getBytes(StandardCharsets.UTF_8).length > MAX_REQUEST_BYTES) {
            throw new ProtocolException("payload_too_large");
        }
        return encoded.toString();
    }

    private static String requiredString(Map<String, Object> values, String name)
            throws ProtocolException {
        Object value = values.get(name);
        if (!(value instanceof String) || ((String) value).trim().isEmpty()) {
            throw new ProtocolException("invalid_" + name);
        }
        return ((String) value).trim();
    }

    private static int requiredInt(Map<String, Object> values, String name)
            throws ProtocolException {
        long value = requiredLong(values, name);
        if (value < Integer.MIN_VALUE || value > Integer.MAX_VALUE) {
            throw new ProtocolException("invalid_" + name);
        }
        return (int) value;
    }

    private static long requiredPositiveLong(Map<String, Object> values, String name)
            throws ProtocolException {
        long value = requiredLong(values, name);
        if (value <= 0) {
            throw new ProtocolException("invalid_" + name);
        }
        return value;
    }

    private static long requiredLong(Map<String, Object> values, String name)
            throws ProtocolException {
        Object value = values.get(name);
        if (!(value instanceof Long)) {
            throw new ProtocolException("invalid_" + name);
        }
        return (Long) value;
    }

    private static Map<String, Object> requiredObject(
            Map<String, Object> values, String name) throws ProtocolException {
        Object value = values.get(name);
        if (!(value instanceof Map)) {
            throw new ProtocolException("invalid_" + name);
        }
        @SuppressWarnings("unchecked")
        Map<String, Object> result = (Map<String, Object>) value;
        return Collections.unmodifiableMap(result);
    }

    private static void writeJson(Object value, StringBuilder output, int depth)
            throws ProtocolException {
        if (depth > MAX_JSON_DEPTH) {
            throw new ProtocolException("json_too_deep");
        }
        if (value == null) {
            output.append("null");
        } else if (value instanceof String) {
            writeString((String) value, output);
        } else if (value instanceof Boolean || value instanceof Number) {
            output.append(value.toString());
        } else if (value instanceof Map) {
            output.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                if (!(entry.getKey() instanceof String)) {
                    throw new ProtocolException("invalid_json_key");
                }
                if (!first) {
                    output.append(',');
                }
                first = false;
                writeString((String) entry.getKey(), output);
                output.append(':');
                writeJson(entry.getValue(), output, depth + 1);
            }
            output.append('}');
        } else if (value instanceof List) {
            output.append('[');
            boolean first = true;
            for (Object item : (List<?>) value) {
                if (!first) {
                    output.append(',');
                }
                first = false;
                writeJson(item, output, depth + 1);
            }
            output.append(']');
        } else {
            throw new ProtocolException("unsupported_json_value");
        }
    }

    private static void writeString(String value, StringBuilder output) {
        output.append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"': output.append("\\\""); break;
                case '\\': output.append("\\\\"); break;
                case '\b': output.append("\\b"); break;
                case '\f': output.append("\\f"); break;
                case '\n': output.append("\\n"); break;
                case '\r': output.append("\\r"); break;
                case '\t': output.append("\\t"); break;
                default:
                    if (character < 0x20) {
                        output.append(String.format("\\u%04x", (int) character));
                    } else {
                        output.append(character);
                    }
            }
        }
        output.append('"');
    }

    private static final class JsonReader {
        private final String input;
        private int position;

        private JsonReader(String input) {
            this.input = input;
        }

        private Object read() throws ProtocolException {
            Object value = readValue(0);
            skipWhitespace();
            if (position != input.length()) {
                throw new ProtocolException("trailing_json");
            }
            return value;
        }

        private Object readValue(int depth) throws ProtocolException {
            if (depth > MAX_JSON_DEPTH) {
                throw new ProtocolException("json_too_deep");
            }
            skipWhitespace();
            if (position >= input.length()) {
                throw new ProtocolException("invalid_json");
            }
            char token = input.charAt(position);
            if (token == '{') return readObject(depth + 1);
            if (token == '[') return readArray(depth + 1);
            if (token == '"') return readString();
            if (token == 't') return readLiteral("true", Boolean.TRUE);
            if (token == 'f') return readLiteral("false", Boolean.FALSE);
            if (token == 'n') return readLiteral("null", null);
            if (token == '-' || Character.isDigit(token)) return readNumber();
            throw new ProtocolException("invalid_json");
        }

        private Map<String, Object> readObject(int depth) throws ProtocolException {
            position++;
            Map<String, Object> result = new LinkedHashMap<String, Object>();
            skipWhitespace();
            if (consume('}')) return result;
            while (true) {
                skipWhitespace();
                if (position >= input.length() || input.charAt(position) != '"') {
                    throw new ProtocolException("invalid_json");
                }
                String key = readString();
                skipWhitespace();
                require(':');
                if (result.put(key, readValue(depth)) != null) {
                    throw new ProtocolException("duplicate_json_key");
                }
                skipWhitespace();
                if (consume('}')) return result;
                require(',');
            }
        }

        private List<Object> readArray(int depth) throws ProtocolException {
            position++;
            List<Object> result = new ArrayList<Object>();
            skipWhitespace();
            if (consume(']')) return result;
            while (true) {
                result.add(readValue(depth));
                skipWhitespace();
                if (consume(']')) return result;
                require(',');
            }
        }

        private String readString() throws ProtocolException {
            require('"');
            StringBuilder result = new StringBuilder();
            while (position < input.length()) {
                char character = input.charAt(position++);
                if (character == '"') return result.toString();
                if (character == '\\') {
                    if (position >= input.length()) throw new ProtocolException("invalid_json");
                    char escaped = input.charAt(position++);
                    switch (escaped) {
                        case '"': result.append('"'); break;
                        case '\\': result.append('\\'); break;
                        case '/': result.append('/'); break;
                        case 'b': result.append('\b'); break;
                        case 'f': result.append('\f'); break;
                        case 'n': result.append('\n'); break;
                        case 'r': result.append('\r'); break;
                        case 't': result.append('\t'); break;
                        case 'u': result.append(readUnicode()); break;
                        default: throw new ProtocolException("invalid_json");
                    }
                } else {
                    if (character < 0x20) throw new ProtocolException("invalid_json");
                    result.append(character);
                }
            }
            throw new ProtocolException("invalid_json");
        }

        private char readUnicode() throws ProtocolException {
            if (position + 4 > input.length()) throw new ProtocolException("invalid_json");
            try {
                char value = (char) Integer.parseInt(input.substring(position, position + 4), 16);
                position += 4;
                return value;
            } catch (NumberFormatException error) {
                throw new ProtocolException("invalid_json");
            }
        }

        private Long readNumber() throws ProtocolException {
            int start = position;
            if (input.charAt(position) == '-') position++;
            int digits = position;
            while (position < input.length() && Character.isDigit(input.charAt(position))) {
                position++;
            }
            if (digits == position) throw new ProtocolException("invalid_json");
            if (position < input.length()
                    && (input.charAt(position) == '.' || input.charAt(position) == 'e'
                    || input.charAt(position) == 'E')) {
                throw new ProtocolException("non_integer_number");
            }
            try {
                return Long.valueOf(input.substring(start, position));
            } catch (NumberFormatException error) {
                throw new ProtocolException("invalid_json");
            }
        }

        private Object readLiteral(String literal, Object value) throws ProtocolException {
            if (!input.startsWith(literal, position)) throw new ProtocolException("invalid_json");
            position += literal.length();
            return value;
        }

        private void skipWhitespace() {
            while (position < input.length() && Character.isWhitespace(input.charAt(position))) {
                position++;
            }
        }

        private boolean consume(char expected) {
            if (position < input.length() && input.charAt(position) == expected) {
                position++;
                return true;
            }
            return false;
        }

        private void require(char expected) throws ProtocolException {
            if (!consume(expected)) throw new ProtocolException("invalid_json");
        }
    }
}
