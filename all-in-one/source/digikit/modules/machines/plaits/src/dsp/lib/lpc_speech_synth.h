// Copyright 2016 Emilie Gillet.
//
// Author: Emilie Gillet (emilie.o.gillet@gmail.com)
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in
// all copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
// THE SOFTWARE.
//
// See http://creativecommons.org/licenses/MIT/ for more information.
//
// -----------------------------------------------------------------------------
//
// PLAITS for the Digitakt II: Plaits' LPC10 speech synth, its word bank and its controller
// (plaits/dsp/speech/lpc_speech_synth.{h,cc}, lpc_speech_synth_controller.{h,cc}, lpc_speech_synth_phonemes.cc,
// lpc_speech_synth_words.{h,cc}) in C99. The word banks' frames are decoded by lib/gen_speech_tables.py into
// lib/speech_tables.c instead of into the arena at run time (see lib/lpc_speech_synth.c).

#ifndef PLAITS_LIB_LPC_SPEECH_SYNTH_H
#define PLAITS_LIB_LPC_SPEECH_SYNTH_H

#define kLPCOrder 10
#define kLPCSpeechSynthNumVowels 5
#define kLPCSpeechSynthNumConsonants 10
#define kLPCSpeechSynthNumPhonemes 15

/* LPCSpeechSynth::Frame as PlayFrame reads it: 1 / period (0.0 for period 0), energy / 256, k0..k9 / their scale. */
typedef struct {
  float inv_period;
  float energy;
  float k[kLPCOrder];
} LPCFrame;

typedef struct {
  float phase_;
  float frequency_;
  float noise_energy_;
  float pulse_energy_;
  float next_sample_;
  int excitation_pulse_sample_index_;
  float k_[kLPCOrder];
  float s_[kLPCOrder + 1];
  /* stmlib's Random::rng_state_ (a global in Plaits, 0x21 at power-up): one per voice here */
  unsigned int rng_state_;
} LPCSpeechSynth;

/* LPCSpeechSynthWordBank: which bank is loaded; its frames and word boundaries are tables (lib/speech_tables.h). */
typedef struct {
  int loaded_bank_;
  int num_frames_;
  int num_words_;
} LPCSpeechSynthWordBank;

typedef struct {
  float clock_phase_;
  float sample_[2];
  float next_sample_[2];
  float gain_;
  LPCSpeechSynth synth_;
  int playback_frame_;
  int last_playback_frame_;
  int remaining_frame_samples_;
  LPCSpeechSynthWordBank word_bank_;
} LPCSpeechSynthController;

#ifdef __cplusplus
extern "C" {
#endif
void lpc_speech_synth_word_bank_reset(LPCSpeechSynthWordBank *b);
/* The controller and its synth (LPCSpeechSynthController::Init; the word bank is reset). */
void lpc_speech_synth_controller_init(LPCSpeechSynthController *c);
void lpc_speech_synth_controller_render(LPCSpeechSynthController *c, int free_running, int trigger, int bank,
                                        float frequency, float prosody_amount, float speed, float address,
                                        float formant_shift, float gain, float *excitation, float *output, int size);
/* A word bank frame (bank b, frame i of it) as PlayFrame reads it; for tests. */
void lpc_word_frame(int bank, int frame, LPCFrame *f);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_LIB_LPC_SPEECH_SYNTH_H
