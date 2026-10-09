package ai.forgebench;

import java.io.IOException;
import java.io.InputStream;
import java.util.Properties;

/** The SDK version, stamped from the pom at build time. */
public final class Version {
    public static final String VERSION = load();

    private Version() {}

    private static String load() {
        try (InputStream in = Version.class.getResourceAsStream("version.properties")) {
            if (in == null) return "0.0.0-dev";
            Properties p = new Properties();
            p.load(in);
            String v = p.getProperty("version");
            return v == null || v.startsWith("${") ? "0.0.0-dev" : v;
        } catch (IOException e) {
            return "0.0.0-dev";
        }
    }
}
