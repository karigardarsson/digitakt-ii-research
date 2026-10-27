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
// PLAITS for the Digitakt II: Plaits' SAMSpeechSynth (plaits/dsp/speech/sam_speech_synth.{h,cc}, SAM-inspired, as in
// Shruthi, Ambika and Braids) in C99: three sine formants reset by a band-limited pulse at the pitch.

#ifndef PLAITS_LIB_SAM_SPEECH_SYNTH_H
#define PLAITS_LIB_SAM_SPEECH_SYNTH_H

#define kSAMNumFormants 3

typedef struct {
  float phase_;
  float frequency_;
  float pulse_next_sample_;
  float pulse_lp_;
  unsigned int formant_phase_[kSAMNumFormants];
  int consonant_samples_;
  float consonant_index_;
} SAMSpeechSynth;

#ifdef __cplusplus
extern "C" {
#endif
void sam_speech_synth_init(SAMSpeechSynth *s);
void sam_speech_synth_render(SAMSpeechSynth *s, int consonant, float frequency, float vowel, float formant_shift,
                             float *excitation, float *output, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_LIB_SAM_SPEECH_SYNTH_H
