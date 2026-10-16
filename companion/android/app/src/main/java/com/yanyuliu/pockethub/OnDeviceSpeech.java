package com.yanyuliu.pockethub;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import com.k2fsa.sherpa.onnx.OfflineModelConfig;
import com.k2fsa.sherpa.onnx.OfflineRecognizer;
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig;
import com.k2fsa.sherpa.onnx.OfflineSenseVoiceModelConfig;
import com.k2fsa.sherpa.onnx.OfflineStream;
import com.k2fsa.sherpa.onnx.OnlineModelConfig;
import com.k2fsa.sherpa.onnx.OnlineRecognizer;
import com.k2fsa.sherpa.onnx.OnlineRecognizerConfig;
import com.k2fsa.sherpa.onnx.OnlineStream;
import com.k2fsa.sherpa.onnx.OnlineZipformer2CtcModelConfig;

/** Bundled streaming preview and desktop-equivalent SenseVoice final recognition. */
final class OnDeviceSpeech {
    interface Callback {
        void ready();
        void level(float value);
        void partial(String text);
        void ended();
        void finished(String text, String error);
    }
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final EmbeddedSpeech speech = new EmbeddedSpeech();
    private final Object modelLock = new Object();
    private ModelSet cachedModels;
    private volatile boolean disposed;
    private long generation;

    boolean recording() { return speech.running(); }

    synchronized void start(Context context, Callback callback) {
        if (disposed) {
            callback.finished(null, "语音已关闭，请重新打开页面后重试"); return;
        }
        if (speech.running()) {
            callback.finished(null, "上一段录音正在收尾，请稍后重试"); return;
        }
        final long session = ++generation;
        final Context app = context.getApplicationContext();
        boolean started = speech.start(PhoneMic::source, () -> {
            try { return new Decoder(models(app)); }
            catch (LinkageError failure) {
                Log.e("PocketHubSpeech", "Bundled speech engine could not load", failure);
                throw new IllegalStateException("内置语音引擎无法加载，请重新安装最新版 App", failure);
            } catch (OutOfMemoryError failure) {
                Log.e("PocketHubSpeech", "Bundled speech model needs more available memory", failure);
                throw new IllegalStateException("手机可用内存不足，请关闭其他应用后重试语音", failure);
            } catch (RuntimeException failure) {
                Log.e("PocketHubSpeech", "Bundled speech model initialization failed", failure);
                throw new IllegalStateException("内置语音模型未能启动，请重试或更新 App", failure);
            }
        }, new EmbeddedSpeech.Callback() {
            private void post(Runnable action) { handler.post(() -> { if (session == generation) action.run(); }); }
            @Override public void ready() { post(callback::ready); }
            @Override public void level(float value) { post(() -> callback.level(value)); }
            @Override public void partial(String text) { post(() -> callback.partial(text)); }
            @Override public void limitReached() { post(callback::ended); }
            @Override public void finished(String text, String error, boolean cancelled) {
                // EmbeddedSpeech has closed the utterance streams and cleared its active session.
                if (disposed && !speech.running()) releaseCachedModels();
                if (!cancelled) post(() -> callback.finished(text, error));
            }
        });
        if (!started) callback.finished(null, "上一段录音正在收尾，请稍后重试");
    }

    void stop() { speech.stop(false); }
    synchronized void cancel() { ++generation; speech.stop(true); }

    synchronized void dispose() {
        if (disposed) return;
        disposed = true;
        cancel();
        if (!speech.running()) new Thread(this::releaseCachedModels, "phone-asr-close").start();
    }

    private ModelSet models(Context context) {
        if (disposed) throw new IllegalStateException("语音已关闭");
        synchronized (modelLock) {
            if (cachedModels != null) return cachedModels;
        }
        // EmbeddedSpeech allows only one active worker. Never hold a UI-visible lock while loading.
        ModelSet loaded = new ModelSet(context);
        synchronized (modelLock) { cachedModels = loaded; }
        return loaded;
    }

    private void releaseCachedModels() {
        ModelSet models;
        synchronized (modelLock) { models = cachedModels; cachedModels = null; }
        if (models != null) {
            try { models.close(); }
            catch (RuntimeException | LinkageError failure) {
                Log.e("PocketHubSpeech", "Bundled speech model cleanup failed", failure);
            }
        }
    }

    private static final class ModelSet {
        private OnlineRecognizer previewRecognizer;
        private OfflineRecognizer finalRecognizer;
        ModelSet(Context context) {
            try {
                OnlineModelConfig previewModel = new OnlineModelConfig();
                previewModel.setZipformer2Ctc(new OnlineZipformer2CtcModelConfig("asr/zh/model.int8.onnx"));
                previewModel.setTokens("asr/zh/tokens.txt");
                previewModel.setNumThreads(2); previewModel.setProvider("cpu"); previewModel.setDebug(false);
                OnlineRecognizerConfig previewConfig = new OnlineRecognizerConfig();
                previewConfig.setModelConfig(previewModel); previewConfig.setEnableEndpoint(false);
                previewConfig.setDecodingMethod("greedy_search");
                previewRecognizer = new OnlineRecognizer(context.getAssets(), previewConfig);

                OfflineSenseVoiceModelConfig senseVoice = new OfflineSenseVoiceModelConfig();
                senseVoice.setModel("asr/sense-voice/model.int8.onnx");
                senseVoice.setLanguage("auto");
                senseVoice.setUseInverseTextNormalization(true);
                OfflineModelConfig finalModel = new OfflineModelConfig();
                finalModel.setSenseVoice(senseVoice);
                finalModel.setTokens("asr/sense-voice/tokens.txt");
                finalModel.setNumThreads(2); finalModel.setProvider("cpu"); finalModel.setDebug(false);
                OfflineRecognizerConfig finalConfig = new OfflineRecognizerConfig();
                finalConfig.setModelConfig(finalModel); finalConfig.setDecodingMethod("greedy_search");
                finalRecognizer = new OfflineRecognizer(context.getAssets(), finalConfig);
            } catch (RuntimeException | LinkageError | OutOfMemoryError failure) {
                try { close(); }
                catch (RuntimeException | LinkageError cleanup) { failure.addSuppressed(cleanup); }
                throw failure;
            }
        }
        void close() {
            try { if (finalRecognizer != null) finalRecognizer.release(); }
            finally { if (previewRecognizer != null) previewRecognizer.release(); }
        }
    }

    private static final class Decoder implements EmbeddedSpeech.Decoder {
        private final ModelSet models;
        private OnlineStream previewStream;
        private OfflineStream finalStream;
        private final FinalSpeechInput finalInput = new FinalSpeechInput(30 * VoiceRecording.SAMPLE_RATE);
        Decoder(ModelSet models) {
            this.models = models;
            try {
                previewStream = models.previewRecognizer.createStream("");
                finalStream = models.finalRecognizer.createStream();
            } catch (RuntimeException | LinkageError | OutOfMemoryError failure) {
                try { close(); }
                catch (RuntimeException | LinkageError cleanup) { failure.addSuppressed(cleanup); }
                throw failure;
            }
        }
        @Override public void samples(float[] pcm) {
            try {
                finalInput.samples(pcm);
                previewStream.acceptWaveform(pcm, VoiceRecording.SAMPLE_RATE);
                while (models.previewRecognizer.isReady(previewStream)) models.previewRecognizer.decode(previewStream);
            }
            catch (RuntimeException | OutOfMemoryError failure) { throw decodingFailure(failure); }
        }
        @Override public String partial() {
            try { return models.previewRecognizer.getResult(previewStream).getText(); }
            catch (RuntimeException | OutOfMemoryError failure) { throw decodingFailure(failure); }
        }
        @Override public String finish() {
            try {
                // Preview text is provisional. Only SenseVoice decides the released utterance.
                return finalInput.finish(new FinalSpeechInput.Recognizer() {
                    @Override public void samples(float[] pcm) {
                        finalStream.acceptWaveform(pcm, VoiceRecording.SAMPLE_RATE);
                    }
                    @Override public String decode() {
                        models.finalRecognizer.decode(finalStream);
                        return models.finalRecognizer.getResult(finalStream).getText();
                    }
                });
            } catch (RuntimeException | OutOfMemoryError failure) { throw decodingFailure(failure); }
        }
        private IllegalStateException decodingFailure(Throwable failure) {
            Log.e("PocketHubSpeech", "Bundled speech decoding failed", failure);
            String message = failure instanceof OutOfMemoryError
                    ? "手机可用内存不足，请关闭其他应用后重试语音" : "手机语音模型处理失败，请重试";
            return new IllegalStateException(message, failure);
        }
        @Override public void close() {
            try { if (finalStream != null) finalStream.release(); }
            finally { if (previewStream != null) previewStream.release(); }
        }
    }
}
