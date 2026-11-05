#include "engine.h"

static sl_u32 magnitude(int n) { return n<0 ? (sl_u32)-n : (sl_u32)n; }


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

/* Native SMART only. The shared detector and Sample Lab policy stay intact.
 * Build prefix supplies the exact existing magnitude/background helpers. */
#include "engine.h"

static sl_u32 smart_attack(const sl_i16 *s,sl_u32 n,sl_u32 ch,
                            sl_u32 rate,sl_u32 at){
    sl_u32 i,k,end=at+rate/100,peak=0,v;
    if(end>n)end=n;
    for(i=at;i<end;i++)for(k=0;k<ch;k++){
        v=magnitude(s[i*ch+k]);if(v>peak)peak=v;
    }
    return peak;
}

/* Overflow recovery: the same admitted attacks, distributed across equal
 * time windows. Retain the earliest attack, then the strongest attack per
 * occupied window (earlier wins ties). Empty windows produce no slice.
 * No fabricated/even cuts, truncation to the beginning, heap or persistent
 * state. This is a best-effort reduction, not an exact beat-count detector. */
static int smart_dense(const sl_i16 *s,sl_u32 n,sl_u32 ch,sl_u32 rate,
                       sl_u32 sensitivity,sl_u32 min_ms,SLRegion *out,
                       sl_u32 capacity,sl_u32 *count){
    sl_u32 starts[SL_MAX_REGIONS]={0},scores[SL_MAX_REGIONS]={0};
    sl_u32 assigned[SL_MAX_REGIONS]={0};
    sl_u32 peak,noise,hop=rate/1000,gap=rate*min_ms/1000;
    sl_u32 i,j,k,limit,total,p,v,fast,previous=0,slow=0,threshold,floor;
    sl_u32 last=0,detected=0,first=capacity,bucket,score,used=0;
    int change;
    noise=onset_background(s,n,ch,rate,&peak);
    floor=peak*(4+(127-sensitivity)/4)/4;if(floor<8)floor=8;
    threshold=128+(127-sensitivity)*4;
    for(i=0;i<n;i+=hop){
        limit=n-i<hop?n:i+hop;total=0;
        for(j=i;j<limit;j++){
            p=0;for(k=0;k<ch;k++){v=magnitude(s[j*ch+k]);if(v>p)p=v;}
            total+=p;
        }
        fast=total*256/(limit-i);
        if(fast>previous+floor && fast>slow+(slow>>8)*threshold+((slow&255)*threshold>>8)+floor){
            p=i>rate/500?i-rate/500:0;
            if((!detected||p-last>=gap) && onset_clear(s,n,ch,rate,p,noise)){
                last=p;detected++;
                /* Match the original detector's too-short final-tail rule. */
                if(n-p>=gap){
                    /* n<=3168000 and capacity<=64: product fits32 bits. */
                    bucket=p*capacity/n;score=smart_attack(s,n,ch,rate,p);
                    if(first==capacity)first=bucket;
                    if(!assigned[bucket] || (bucket!=first && score>scores[bucket])){
                        starts[bucket]=p;scores[bucket]=score;assigned[bucket]=1;
                    }
                }
            }
        }
        change=(int)fast-(int)slow;
        slow=(sl_u32)((int)slow+change/16);previous=fast;
    }
    for(i=0;i<capacity;i++)if(assigned[i]){
        out[used].start=starts[i];out[used].end=n;
        if(used)out[used-1].end=starts[i];
        used++;
    }
    if(!used)return SL_LIMIT; /* Defensive: never commit an empty grid. */
    *count=used;return SL_OK;
}

int smart_detect(const sl_i16 *s,sl_u32 n,sl_u32 ch,sl_u32 rate,
                  sl_u32 sensitivity,sl_u32 min_ms,SLRegion *out,
                  sl_u32 capacity,sl_u32 *count){
    int rc=sl_detect(s,n,ch,rate,sensitivity,min_ms,out,capacity,count);
    if(rc!=SL_LIMIT)return rc;
    return smart_dense(s,n,ch,rate,sensitivity,min_ms,out,capacity,count);
}
