#include "engine.h"

static sl_u32 magnitude(int n) { return n<0 ? (sl_u32)-n : (sl_u32)n; }
static int valid(const sl_i16 *s,sl_u32 n,sl_u32 ch) {
    return s && n && n<=SL_MAX_FRAMES && (ch==1 || ch==2);
}
static int target(const sl_i16 *s,sl_u32 n,sl_u32 ch,sl_u32 a,sl_u32 b,
                  sl_i16 *d,sl_u32 capacity) {
    sl_uptr sb,se,db,de;
    if (!valid(s,n,ch) || !d || a>=b || b>n) return SL_INVALID;
    if (capacity<b-a) return SL_CAPACITY;
    sb=(sl_uptr)s; se=sb+(sl_uptr)n*ch*2;
    db=(sl_uptr)d; de=db+(sl_uptr)(b-a)*ch*2;
    if (se<sb || de<db || (db<se && sb<de)) return SL_OVERLAP;
    return SL_OK;
}
static sl_u32 edge_cost(const sl_i16 *s,sl_u32 n,sl_u32 ch,sl_u32 p) {
    sl_u32 k,cost=0;
    if (!p || p==n) {
        for(k=0;k<ch;k++)cost+=4*magnitude(s[(p ? n-1 : 0)*ch+k]);
    } else for(k=0;k<ch;k++) {
        int a=s[(p-1)*ch+k],b=s[p*ch+k];
        cost+=magnitude(a)+magnitude(b)+magnitude(b-a);
    }
    return cost;
}
int sl_snap(const sl_i16 *s,sl_u32 n,sl_u32 ch,sl_u32 p,sl_u32 radius,
            sl_u32 low,sl_u32 high,sl_u32 *out) {
    sl_u32 left,right,i,best,cost,score,dist,bestdist;
    if (!valid(s,n,ch) || !out || low>high || high>n || p<low || p>high || radius>n)
        return SL_INVALID;
    left=p-low>radius ? p-radius : low;
    right=high-p>radius ? p+radius : high;
    best=p;cost=edge_cost(s,n,ch,p);bestdist=0;
    for(i=left;i<=right;i++) {
        score=edge_cost(s,n,ch,i);dist=i>p ? i-p : p-i;
        if(score<cost || (score==cost && (dist<bestdist || (dist==bestdist && i<best)))) {
            best=i;cost=score;bestdist=dist;
        }
    }
    *out=best;return SL_OK;
}
int sl_detect(const sl_i16 *s,sl_u32 n,sl_u32 ch,sl_u32 rate,
              sl_u32 sensitivity,sl_u32 min_ms,SLRegion *out,sl_u32 capacity,sl_u32 *count) {
    SLRegion temp[SL_MAX_REGIONS];
    sl_u32 i,j,k,peak=0,hop,gap,floor,fast,previous=0,slow=0,total,p,used=0,limit,lookback;
    int change;
    if (!count || !out || !valid(s,n,ch) || rate<8000 || rate>48000 ||
        sensitivity>127 || min_ms<10 || min_ms>1000 || !capacity || capacity>SL_MAX_REGIONS)
        return SL_INVALID;
    for(i=0;i<n*ch;i++) { p=magnitude(s[i]);if(p>peak)peak=p; }
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
            if(!used || p-temp[used-1].start>=gap) {
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
int sl_copy(const sl_i16 *s,sl_u32 n,sl_u32 ch,sl_u32 a,sl_u32 b,sl_i16 *d,sl_u32 cap) {
    sl_u32 i;int result=target(s,n,ch,a,b,d,cap);
    if(result)return result;
    for(i=0;i<(b-a)*ch;i++)d[i]=s[a*ch+i];
    return SL_OK;
}
static sl_u32 weight(sl_u32 i,sl_u32 frames) {
    sl_u32 t=i*32768/(frames-1),w=t*t>>15;
    return w*(98304-2*t)>>15;
}
static sl_i16 mix(int a,int b,sl_u32 w) {
    int x=a*(int)(32768-w)+b*(int)w;
    return (sl_i16)(x<0 ? -((-x+16384)/32768) : (x+16384)/32768);
}
int sl_loop(const sl_i16 *s,sl_u32 n,sl_u32 ch,sl_u32 a,sl_u32 b,
            sl_u32 fade,sl_i16 *d,sl_u32 cap) {
    sl_u32 i,k,w;int before,result=target(s,n,ch,a,b,d,cap);
    if(result)return result;
    if(fade && (fade<2 || fade>(b-a)/2 || fade>32768))return SL_INVALID;
    before=a>=fade;
    if(fade && !before && n-b<fade)return SL_NO_CONTEXT;
    result=sl_copy(s,n,ch,a,b,d,cap);if(result || !fade)return result;
    for(i=0;i<fade;i++) {
        w=weight(i,fade);
        for(k=0;k<ch;k++) {
            if(before)d[(b-a-fade+i)*ch+k]=mix(s[(b-fade+i)*ch+k],s[(a-fade+i)*ch+k],w);
            else d[i*ch+k]=mix(s[(b+i)*ch+k],s[(a+i)*ch+k],w);
        }
    }
    return SL_OK;
}
int sl_beat_frames(sl_u32 rate,sl_u32 bpm_milli,sl_u32 beats,sl_u32 *out) {
    sl_u32 numerator,q,r;
    if(!out || rate<8000 || rate>48000 || bpm_milli<20000 || bpm_milli>300000 ||
        !beats || beats>512)return SL_INVALID;
    numerator=rate*60000;q=numerator/bpm_milli;r=numerator%bpm_milli;
    *out=q*beats+(r*beats+bpm_milli/2)/bpm_milli;
    return SL_OK;
}
void sl_invalidate(SLSession *s) {
    if(s) {s->source=0;s->frames=0;s->count=0;s->selected=0;s->generation=0;}
}
int sl_open(SLSession *s,const sl_i16 *pcm,sl_u32 n,sl_u32 ch,sl_u32 rate,sl_u32 generation) {
    if(!s || !valid(pcm,n,ch) || rate<8000 || rate>48000 || !generation)return SL_INVALID;
    s->source=pcm;s->frames=n;s->channels=ch;s->rate=rate;s->generation=generation;
    s->count=1;s->selected=0;s->regions[0].start=0;s->regions[0].end=n;return SL_OK;
}
static int live(SLSession *s,sl_u32 generation) {
    return s && s->source && generation && s->generation==generation;
}
int sl_analyze(SLSession *s,sl_u32 generation,sl_u32 sensitivity,sl_u32 min_ms) {
    SLRegion regions[SL_MAX_REGIONS];sl_u32 count,i;int result;
    if(!live(s,generation))return SL_STALE;
    result=sl_detect(s->source,s->frames,s->channels,s->rate,sensitivity,min_ms,
        regions,SL_MAX_REGIONS,&count);
    if(result)return result;
    for(i=0;i<count;i++)s->regions[i]=regions[i];
    s->count=count;s->selected=0;return SL_OK;
}
int sl_select(SLSession *s,sl_u32 generation,sl_u32 region) {
    if(!live(s,generation))return SL_STALE;
    if(region>=s->count)return SL_INVALID;
    s->selected=region;return SL_OK;
}
int sl_render(SLSession *s,sl_u32 generation,sl_u32 fade,sl_i16 *dst,sl_u32 cap) {
    SLRegion r;
    if(!live(s,generation))return SL_STALE;
    if(!s->count || s->selected>=s->count)return SL_INVALID;
    r=s->regions[s->selected];
    return sl_loop(s->source,s->frames,s->channels,r.start,r.end,fade,dst,cap);
}
