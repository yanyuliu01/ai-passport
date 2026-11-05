package com.yanyuliu.pockethub;

import java.net.URI;
import java.net.URL;

/** Exact account release address and Runtime binding; no credentials are embedded in URLs. */
final class PrivateReleaseRoute {
    private static final String PREFIX = "/v1/private-releases/apps/";
    private PrivateReleaseRoute() { }

    static boolean looksPrivate(String address) {
        try {
            String path = URI.create(address).getRawPath();
            return path != null && path.startsWith(PREFIX);
        } catch (RuntimeException invalid) { return false; }
    }

    static boolean matches(URL url, String account, String sha) {
        try {
            URI uri = url.toURI(), origin = URI.create(AccountManager.SERVER);
            return account != null && account.matches("[a-f0-9]{32}")
                && sha != null && sha.matches("[a-f0-9]{64}")
                && "https".equals(uri.getScheme()) && origin.getHost().equalsIgnoreCase(uri.getHost())
                && (uri.getPort() == -1 || uri.getPort() == 443) && uri.getRawUserInfo() == null
                && uri.getRawQuery() == null && uri.getRawFragment() == null
                && (PREFIX + account + "/" + sha + ".apk").equals(uri.getRawPath());
        } catch (Exception invalid) { return false; }
    }

    static boolean targetMatches(String target, String account) {
        return account != null && account.matches("[a-f0-9]{32}") && target != null
            && target.matches("relay://[a-f0-9]{32}\\." + account);
    }

    static boolean completionAllowed(String originalUrl, String originalTargetToken, String originalSession,
                                     String currentUrl, String currentTargetToken, String currentSession) {
        return originalUrl != null && originalTargetToken != null && originalSession != null
            && !originalSession.isEmpty() && originalUrl.equals(currentUrl)
            && originalTargetToken.equals(currentTargetToken) && originalSession.equals(currentSession);
    }
}
