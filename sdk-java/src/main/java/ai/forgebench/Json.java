package ai.forgebench;

import com.fasterxml.jackson.annotation.JsonAutoDetect;
import com.fasterxml.jackson.annotation.PropertyAccessor;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.MapperFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.json.JsonMapper;

/** The SDK's two Jackson mappers. */
final class Json {
    /**
     * Reads responses into the SDK's types: snake_case wire names onto
     * camelCase fields, unknown fields ignored (forward-compatible with server
     * additions), fields only — computed getters never bind.
     */
    static final ObjectMapper READER = JsonMapper.builder()
            .propertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)
            .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            .disable(MapperFeature.USE_GETTERS_AS_SETTERS)
            .visibility(PropertyAccessor.ALL, JsonAutoDetect.Visibility.NONE)
            .visibility(PropertyAccessor.FIELD, JsonAutoDetect.Visibility.ANY)
            .build();

    /**
     * Writes request bodies. Plain defaults, so caller objects inside
     * {@code extraBody} or message content serialize the way Jackson users expect.
     */
    static final ObjectMapper WRITER = new ObjectMapper();

    private Json() {}
}
