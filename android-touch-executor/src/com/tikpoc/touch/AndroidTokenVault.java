package com.tikpoc.touch;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

public final class AndroidTokenVault implements DeviceProvisioning.Vault {
    private static final String KEYSTORE = "AndroidKeyStore";
    private static final String ALIAS = "tikpoc_device_credential";
    private static final String PREFS = "tikpoc_device_vault";
    private static final String CIPHERTEXT = "credential_ciphertext";
    private static final String IV = "credential_iv";
    private final SharedPreferences preferences;

    public AndroidTokenVault(Context context) {
        preferences = context.getApplicationContext()
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    @Override
    public void save(String accessToken) throws Exception {
        SecretKey key = key();
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key);
        byte[] encrypted = cipher.doFinal(accessToken.getBytes(StandardCharsets.UTF_8));
        if (!preferences.edit()
                .putString(CIPHERTEXT, Base64.encodeToString(encrypted, Base64.NO_WRAP))
                .putString(IV, Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP))
                .commit()) {
            throw new IllegalStateException("credential persistence failed");
        }
    }

    @Override
    public String load() throws Exception {
        String encrypted = preferences.getString(CIPHERTEXT, "");
        String iv = preferences.getString(IV, "");
        if (encrypted == null || encrypted.isEmpty() || iv == null || iv.isEmpty()) return "";
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(),
                new GCMParameterSpec(128, Base64.decode(iv, Base64.NO_WRAP)));
        byte[] plain = cipher.doFinal(Base64.decode(encrypted, Base64.NO_WRAP));
        return new String(plain, StandardCharsets.UTF_8);
    }

    private static SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance(KEYSTORE);
        store.load(null);
        if (store.containsAlias(ALIAS)) {
            return (SecretKey) store.getKey(ALIAS, null);
        }
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE);
        generator.init(new KeyGenParameterSpec.Builder(
                ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build());
        return generator.generateKey();
    }
}
