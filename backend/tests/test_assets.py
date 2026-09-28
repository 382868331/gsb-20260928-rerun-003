import numpy as np

from conftest import dominant_freq, make_tone


def test_resample_unifies_sample_rate(library, tmp_path):
    import soundfile as sf

    p = tmp_path / "a44k1.wav"
    sr0 = 44100
    sf.write(p, make_tone(1000.0, sr0, 0.5), sr0)
    meta = library.import_file(p, project_sr=48000)
    assert meta.sample_rate == 48000
    assert meta.source_sample_rate == 44100
    audio = library.load_audio(meta.id)
    assert audio.shape[0] == int(round(0.5 * 48000))
    # 重采样后频率内容保持可辨认
    assert abs(dominant_freq(audio, 48000) - 1000.0) < 20.0


def test_stereo_preserved(library, tmp_path):
    import soundfile as sf

    sr = 48000
    stereo = np.stack([make_tone(300, sr), make_tone(600, sr)], axis=1)
    p = tmp_path / "st.wav"
    sf.write(p, stereo, sr)
    meta = library.import_file(p, sr)
    assert meta.channels == 2
    audio = library.load_audio(meta.id)
    assert audio.shape[1] == 2
