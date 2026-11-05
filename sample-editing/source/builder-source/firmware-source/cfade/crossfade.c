/* Original OS1.17 inline sample-loop adapter. AGPL-3.0-or-later.
 * Never shortens the primary loop. All blending precedes native decimation.
 * cf_render owns its scratch/context; no source PCM allocation or borrowing.
 */
#include "dsp_util.h"
typedef unsigned int U;
typedef struct { U w[118]; } Record;
typedef struct {
    Record primary, head;
    float ignored[32];
    U ready, pointer, start[2], end[2], expected[2], fade, reverse, wrapped, trigger,slot,generation,owned_slot;
} Crossfade;
extern void cf_native(Record *,float *,int);
extern volatile U cf_generations[1025];
extern void cf_slot(U,U *);

static void copy(U *dst,const U *src,int n){int k;for(k=0;k<n;k++)dst[k]=src[k];}
/* Biased signed order avoids the compiler's unproven COMP(U) encoding. */
static U below(U a,U b){return (int)(a^0x80000000u)<(int)(b^0x80000000u);}
static float uf(U x){return itof_nonneg((int)(x>>16))*65536.0f+itof_nonneg((int)(x&65535u));}
static float sf(int x){return x<0?-uf((U)(-(x+1))+1u):uf((U)x);}
static float distance(const U *a,const U *b){
    U lo=a[0]-b[0],hi=a[1]-b[1]-below(a[0],b[0]);
    /* Reuse Plaits' proven bit conversion, avoiding both unsigned conversion
     * semantics and the unproven compressed FLOAT instruction shape. */
    return sf((int)hi)*2.0f+uf(lo)*(1.0f/2147483648.0f);
}
static void add_frames(U *p,U n){U lo=p[0];p[0]+=n<<31;p[1]+=(n>>1)+below(p[0],lo);}
static void sub_frames(U *p,U n){U lo=p[0];p[0]-=n<<31;p[1]-=(n>>1)+below(lo,p[0]);}
static void add_q(U *p,const U *n){U lo=p[0];p[0]+=n[0];p[1]+=n[1]+below(p[0],lo);}
static void sub_q(U *p,const U *n){U lo=p[0];p[0]-=n[0];p[1]-=n[1]+below(lo,p[0]);}

/* Return1 = wrote64 work samples and advanced only native sampler state.
 * Return0 = caller must run original stock renderer (no record writes).
 * The entry checks OFF before calling C; this guard also serves target tests.
 */
int cf_render(Crossfade *s,Record *r,U control){
    U *w=r->w,*a=w+0x190/4,*b=w+0x1a0/4,*p=w+0x1b0/4;
    U span[2],info[5],fade,reset,generation,owned,reverse=(w[0x1b8/4]>>24)&255;
    float length,phase,step,d,weight,inv,width;
    int k,turns,wrapped;
    control>>=8;if((int)control>127)control=127;
    if(!control||!w[0]||!(w[0x1b8/4]&255)||!(w[0x1bc/4]&255)){
        s->ready=0;return 0;
    }
    if((int)s->slot<0||(int)s->slot>1024){s->ready=0;return 0;}
    /* SAMP may change while an older bus is still sounding. Retain that
     * bus's verified source identity; never guard it using the new frame ID. */
    owned=s->ready&&s->pointer==w[0]&&!s->trigger?s->owned_slot:s->slot;
    generation=cf_generations[owned];if(generation&1){s->ready=0;return 0;}
    cf_slot(owned,info);
    if((w[0]!=info[0]&&w[0]!=info[1])||w[0x188/4]!=info[2]||w[0x184/4]!=info[3]||cf_generations[owned]!=generation){
        s->ready=0;return 0;
    }
    span[0]=b[0];span[1]=b[1];sub_q(span,a);
    length=distance(b,a);
    if(length<4.0f)return 0;
    fade=(U)(uf(w[0x184/4])*0.001f*uf(control));
    if(uf(fade)>length*.25f)fade=(U)(length*.25f);
    if(!fade)return 0;
    width=uf(fade);
    phase=distance(p,a);
    step=distance(w+0x1a8/4,(const U[2]){0,0});
    /* Entire work block outside either fade window: use the ORIGINAL reader,
     * not two private renders. Re-seed the private head on the next window. */
    if((step>=0.0f&&phase>=width&&phase+step*64.0f<length-width)||
       (step<0.0f&&phase<=length-width&&phase+step*64.0f>width)){
        s->ready=0;return 0;
    }
    reset=!s->ready||s->trigger||s->generation!=generation||s->pointer!=w[0]||s->fade!=fade||s->reverse!=reverse||
        s->start[0]!=a[0]||s->start[1]!=a[1]||s->end[0]!=b[0]||s->end[1]!=b[1]||
        s->expected[0]!=p[0]||s->expected[1]!=p[1];
    copy(s->primary.w,w,118);
    if(reset){
        copy(s->head.w,w,118);
        if(reverse){sub_frames(s->head.w+0x1b0/4,fade);
            if(distance(s->head.w+0x1b0/4,a)<0.0f)add_q(s->head.w+0x1b0/4,span);
        }else{add_frames(s->head.w+0x1b0/4,fade);
            if(distance(s->head.w+0x1b0/4,b)>=0.0f)sub_q(s->head.w+0x1b0/4,span);
        }
        /* Same native reseed bit exercised by the retained reader experiment. */
        s->head.w[0x17c/4]|=0x00010000u;
        s->wrapped=0;
    }else{
        /* Native pitch/region controls, not history, follow each new frame. */
        copy(s->head.w+0x184/4,w+0x184/4,(0x1b0-0x184)/4);
        s->head.w[0x1bc/4]=w[0x1bc/4];
    }
    cf_native(&s->primary,s->ignored,32);
    cf_native(&s->head,s->ignored,32);
    if(cf_generations[owned]!=generation){s->ready=0;return 0;}
    inv=1.0f/width;wrapped=(int)s->wrapped;
    for(k=0;k<64;k++){
        float x=((float *)(s->primary.w+1))[k];
        float y=((float *)(s->head.w+1))[k];
        d=phase<length-phase?phase:length-phase;
        weight=1.0f-d*inv;
        if(phase<0.0f||phase>=length||weight<0.0f)weight=0.0f;
        /* Keep the first incoming region's original note attack. */
        if(!wrapped&&((!reverse&&phase<width)||(reverse&&length-phase<width)))weight=0.0f;
        ((float *)(w+1))[k]=weight>0.0f?(1.0f-weight)*x+weight*y:x;
        phase+=step;
        if((step>=0.0f&&phase>=length)||(step<0.0f&&phase<0.0f)){
            turns=(int)(phase/length);phase-=sf(turns)*length;
            if(phase<0.0f)phase+=length;
            wrapped=1;
        }
    }
    /* Preserve the REAL record's decimator history0x104..0x17b. It has not
     * run yet. Native source flags/previous sample/cursor DO advance once. */
    copy(w+0x17c/4,s->primary.w+0x17c/4,(0x1d8-0x17c)/4);
    s->pointer=w[0];copy(s->start,a,2);copy(s->end,b,2);copy(s->expected,p,2);
    s->fade=fade;s->reverse=reverse;s->wrapped=(U)wrapped;s->generation=generation;s->owned_slot=owned;s->ready=1;
    return 1;
}
