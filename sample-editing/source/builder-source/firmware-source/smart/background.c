#include "engine.h"

static sl_u32 magnitude(int n) { return n<0 ? (sl_u32)-n : (sl_u32)n; }
static int valid(const sl_i16 *s,sl_u32 n,sl_u32 ch) {
    return s && n && n<=SL_MAX_FRAMES && (ch==1 || ch==2);
}

/* Quantized20th-percentile1ms envelope, only in recordings with deep quiet.
 * A208-bin logarithmic histogram includes the signed16-bit -32768 extreme.
 * Return0 disables the gate when the lower envelope is ongoing audio. */
static sl_u32 onset_background(const sl_i16 *s,sl_u32 n,sl_u32 ch,
                              sl_u32 rate,sl_u32 *peak) {
    sl_u32 bins[208]={0},i,j,k,hop=rate/1000,limit,total,v,a,shift;
    sl_u32 windows=0,rank,sum=0,noise=0;
    *peak=0;
    for(i=0;i<n;i+=hop){
        limit=n-i<hop?n:i+hop;total=0;
        for(j=i;j<limit;j++){
            v=0;for(k=0;k<ch;k++){
                a=magnitude(s[j*ch+k]);if(a>*peak)*peak=a;if(a>v)v=a;
            }total+=v;
        }
        a=total/(limit-i);shift=0;
        while(a>=16){a>>=1;shift++;}
        bins[shift*16+a]++;windows++;
    }
    rank=(windows+4)/5;
    for(i=0;i<208;i++)if((sum+=bins[i])>=rank){
        noise=(((i%16)+1)<<(i/16))-1;break;
    }
    return noise&&noise<=*peak/64?noise:0;
}
static int onset_clear(const sl_i16 *s,sl_u32 n,sl_u32 ch,
                       sl_u32 rate,sl_u32 at,sl_u32 noise){
    sl_u32 i,j,k,hop=rate/1000,limit,total,v,a;
    if(!noise)return 1;
    for(i=at;i<n&&i<at+hop*12;i+=hop){
        limit=n-i<hop?n:i+hop;total=0;
        for(j=i;j<limit;j++){
            v=0;for(k=0;k<ch;k++){a=magnitude(s[j*ch+k]);if(a>v)v=a;}
            total+=v;
        }
        if(total/(limit-i)>=noise*16)return 1;
    }
    return 0;
}

int sl_detect(const sl_i16 *s,sl_u32 n,sl_u32 ch,sl_u32 rate,
              sl_u32 sensitivity,sl_u32 min_ms,SLRegion *out,sl_u32 capacity,sl_u32 *count) {
    SLRegion temp[SL_MAX_REGIONS];
    sl_u32 i,j,k,peak=0,hop,gap,floor,fast,previous=0,slow=0,total,p,used=0,limit,lookback;
    int change;sl_u32 noise;
    if (!count || !out || !valid(s,n,ch) || rate<8000 || rate>48000 ||
        sensitivity>127 || min_ms<10 || min_ms>1000 || !capacity || capacity>SL_MAX_REGIONS)
        return SL_INVALID;
    noise=onset_background(s,n,ch,rate,&peak);
    if (!peak) { *count=0;return SL_OK; }
    hop=rate/1000;gap=rate*min_ms/1000;
    /* Q8 envelope avoids losing weak attacks by rounding every 1ms average
     * to a whole PCM unit. Keep multiplication bounded for 16-bit input. */
    floor=peak*(4+(127-sensitivity)/4)/4;if(floor<8)floor=8;
    lookback=rate/500;
    for(i=0;i<n;i+=hop) {
        limit=n-i<hop ? n : i+hop;total=0;
        for(j=i;j<limit;j++) {
            p=0;
            for(k=0;k<ch;k++) {sl_u32 v=magnitude(s[j*ch+k]);if(v>p)p=v;}
            total+=p;
        }
        fast=total*256/(limit-i);
        p=128+(127-sensitivity)*4;
        if(fast>previous+floor && fast>slow+(slow>>8)*p+((slow&255)*p>>8)+floor) {
            p=i>lookback ? i-lookback : 0;
            if((!used || p-temp[used-1].start>=gap) && onset_clear(s,n,ch,rate,p,noise)) {
                if(used==capacity)return SL_LIMIT;
                temp[used].start=p;temp[used].end=n;used++;
            }
        }
        change=(int)fast-(int)slow;
        slow=(sl_u32)((int)slow+change/16);previous=fast;
    }
    if(!used) {temp[0].start=0;temp[0].end=n;used=1;}
    /* A tail too short to be useful joins the preceding region. */
    if(used>1 && n-temp[used-1].start<gap)used--;
    for(i=1;i<used;i++)temp[i-1].end=temp[i].start;
    for(i=0;i<used;i++)out[i]=temp[i];
    *count=used;return SL_OK;
}
