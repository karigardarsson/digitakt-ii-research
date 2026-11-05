#include "visual.h"
#define BLOCK 128u
static sl_u32 leaves(sl_u32 n){sl_u32 v=1;while(v<(n+BLOCK-1)/BLOCK)v*=2;return v;}
sl_u32 sl_wave_bytes(sl_u32 n){return !n||n>SL_MAX_FRAMES?0:leaves(n)*2*sizeof(SLPeak);}
static SLPeak empty(void){SLPeak p={32767,-32768};return p;}
static void merge(SLPeak *a,SLPeak b){if(b.low<a->low)a->low=b.low;if(b.high>a->high)a->high=b.high;}
static void raw(const SLWave *w,sl_u32 a,sl_u32 b,SLPeak *p){
    sl_u32 i,k;int v;
    for(i=a;i<b;i++)for(k=0;k<w->channels;k++){
        v=w->source[i*w->channels+k];if(v<p->low)p->low=(sl_i16)v;if(v>p->high)p->high=(sl_i16)v;
    }
}
int sl_wave_build(SLWave *w,const sl_i16 *pcm,sl_u32 n,sl_u32 ch,void *mem,sl_u32 bytes,volatile sl_u32 *cancel){
    sl_u32 i,b;SLWave t;int peak;
    if(!w||!pcm||!mem||!sl_wave_bytes(n)||(ch!=1&&ch!=2)||bytes<sl_wave_bytes(n))return SL_INVALID;
    w->source=0;t.source=pcm;t.frames=n;t.channels=ch;t.leaves=leaves(n);t.tree=mem;t.peak=0;
    for(i=0;i<t.leaves;i++){
        if(cancel&&*cancel)return -10;
        t.tree[t.leaves+i]=empty();b=(i+1)*BLOCK;if(b>n)b=n;
        if(i*BLOCK<n)raw(&t,i*BLOCK,b,&t.tree[t.leaves+i]);
    }
    for(i=t.leaves-1;i;i--){t.tree[i]=t.tree[i*2];merge(&t.tree[i],t.tree[i*2+1]);}
    peak=-(int)t.tree[1].low;t.peak=(sl_u32)(peak>t.tree[1].high?peak:t.tree[1].high);
    *w=t;return SL_OK;
}
int sl_wave_range(const SLWave *w,sl_u32 a,sl_u32 b,SLPeak *out){
    sl_u32 left,right,lo,hi;SLPeak p=empty();
    if(!w||!w->source||!out||a>=b||b>w->frames)return SL_INVALID;
    left=(a+BLOCK-1)/BLOCK;right=b/BLOCK;
    if(left>=right){raw(w,a,b,&p);*out=p;return SL_OK;}
    raw(w,a,left*BLOCK,&p);raw(w,right*BLOCK,b,&p);
    lo=left+w->leaves;hi=right+w->leaves;
    while(lo<hi){if(lo&1)merge(&p,w->tree[lo++]);if(hi&1)merge(&p,w->tree[--hi]);lo/=2;hi/=2;}
    *out=p;return SL_OK;
}
int sl_wave_columns(const SLWave *w,sl_u32 start,sl_u32 span,SLPeak *out,sl_u32 width){
    sl_u32 i,a,b;
    if(!w||!w->source||!out||!span||start>=w->frames||span>w->frames-start||!width||width>128)return SL_INVALID;
    for(i=0;i<width;i++){
        a=start+span*i/width;b=start+span*(i+1)/width;if(b<=a)b=a+1;
        if(sl_wave_range(w,a,b,&out[i]))return SL_INVALID;
    }
    return SL_OK;
}
