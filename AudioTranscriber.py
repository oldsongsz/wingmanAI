import whisper
import torch
import wave
import os
import threading
from tempfile import NamedTemporaryFile
import custom_speech_recognition as sr
import io
from datetime import timedelta
#import pyaudiowpatch as pyaudio
import pyaudio
from heapq import merge

PHRASE_TIMEOUT = 3.05

MAX_PHRASES = 30

class AudioTranscriber:
    def __init__(self, mic_source, speaker_source):
        self.transcript_data = {"You": [], "Speaker": []}
        self.transcript_changed_event = threading.Event()
        self.audio_model = whisper.load_model('base.en')
        print(f'Whisper running on device: {self.audio_model.device}')
        self.should_continue = True
        self.audio_sources = {
            "You": {
                "sample_rate": mic_source.SAMPLE_RATE,
                "sample_width": mic_source.SAMPLE_WIDTH,
                "channels": mic_source.channels,
                "last_sample": bytes(),
                "last_spoken": None,
                "new_phrase": True,
                "process_data_func": self.process_mic_data
            },
            "Speaker": {
                "sample_rate": speaker_source.SAMPLE_RATE,
                "sample_width": speaker_source.SAMPLE_WIDTH,
                "channels": speaker_source.channels,
                "last_sample": bytes(),
                "last_spoken": None,
                "new_phrase": True,
                "process_data_func": self.process_speaker_data
            }
        }

    def transcribe_audio_queue(self, audio_queue):
        while self.should_continue:
            while self.should_continue:
                who_spoke, data, time_spoken = audio_queue.get()
                self.update_last_sample_and_phrase_status(who_spoke, data, time_spoken)
                source_info = self.audio_sources[who_spoke]
                temp_file = source_info["process_data_func"](source_info["last_sample"])
                text = self.get_transcription(temp_file)

                if text != '' and text.lower() != 'you':
                    self.update_transcript(who_spoke, text, time_spoken)
                    self.transcript_changed_event.set()
        print('Stopping')

    def update_last_sample_and_phrase_status(self, who_spoke, data, time_spoken):
        source_info = self.audio_sources[who_spoke]
        if source_info["last_spoken"] and time_spoken - source_info["last_spoken"] > timedelta(seconds=PHRASE_TIMEOUT):
            source_info["last_sample"] = bytes()
            source_info["new_phrase"] = True
        else:
            source_info["new_phrase"] = False

        source_info["last_sample"] += data
        source_info["last_spoken"] = time_spoken 

    def process_mic_data(self, data):
        temp_file = NamedTemporaryFile().name
        audio_data = sr.AudioData(data, self.audio_sources["You"]["sample_rate"], self.audio_sources["You"]["sample_width"])
        wav_data = io.BytesIO(audio_data.get_wav_data())
        with open(temp_file, 'w+b') as f:
            f.write(wav_data.read())
        return temp_file

    def process_speaker_data(self, data):
        temp_file = NamedTemporaryFile().name
        with wave.open(temp_file, 'wb') as wf:
            wf.setnchannels(self.audio_sources["Speaker"]["channels"])
            p = pyaudio.PyAudio()
            wf.setsampwidth(p.get_sample_size(pyaudio.paInt16))
            wf.setframerate(self.audio_sources["Speaker"]["sample_rate"])
            wf.writeframes(data)
        return temp_file

    def get_transcription(self, file_path):
        result = self.audio_model.transcribe(file_path, fp16=torch.cuda.is_available())
        return result['text'].strip()

    def update_transcript(self, who_spoke, text, time_spoken):
        source_info = self.audio_sources[who_spoke]
        transcript = self.transcript_data[who_spoke]

        if source_info["new_phrase"] or len(transcript) == 0:
            if len(transcript) > MAX_PHRASES:
                transcript.pop(-1)
            transcript.insert(0, (f"{who_spoke}: [{text}]\n\n", time_spoken))
        else:
            transcript[0] = (f"{who_spoke}: [{text}]\n\n", time_spoken)

    def get_transcript(self, username="You", speakername="Speaker"):
        combined_transcript = list(merge(
            self.transcript_data["You"], self.transcript_data["Speaker"], 
            key=lambda x: x[1], reverse=True))
        combined_transcript = combined_transcript[:MAX_PHRASES]
        combined_transcript = combined_transcript[::-1]
        formatted = "\n\n".join([f'{username if t[0].startswith("You") else speakername}: "{t[0][t[0].index(":")+2:-3]}" ' for t in combined_transcript])
        formatted = formatted.replace("[", "")
        return formatted

    def get_speaker_transcript(self):
        transcript = list(merge(self.transcript_data["Speaker"], key=lambda x: x[1], reverse=True))
        text_only = []
        for item in transcript:
            text = item[0]
            extracted_text = text.split("[")[1].split("]")[0]
            text_only.append(extracted_text)
        text_string = " ".join(text_only)
        return text_string
    
    def clear_transcript_data(self):
        self.transcript_data["You"].clear()
        self.transcript_data["Speaker"].clear()

        self.audio_sources["You"]["last_sample"] = bytes()
        self.audio_sources["Speaker"]["last_sample"] = bytes()

        self.audio_sources["You"]["new_phrase"] = True
        self.audio_sources["Speaker"]["new_phrase"] = True


    def stop(self):
        self.should_continue = False