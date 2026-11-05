/* Native Sample Lab controller. Private OS1.17 integration, AGPL-3.0-or-later.
 * UI owns one context reference; native queued callables own their own refs.
 * Workers never retain or dereference the screen object. */
#include "engine.h"
#include "visual.h"
#define N(a,t) ((t)(sl_uptr)(a))
typedef sl_u32 U;
typedef struct {U data[2],manager,invoke;} Fn;
typedef struct {U vt,w,h,stride,pixels,aux,owns;} Bitmap;
typedef struct {void *raw,*control;} Pair;
typedef struct Lab Lab;
struct Lab {
    U refs;volatile U cancel,busy,status;
    void *view,*model,*settings,*project;U project_tag,epoch,operation;volatile U capture_stage;
    SLSession session;sl_i16 *source,*output;
    U output_frames,staged,preview,kept,revision,rendered_revision;
    U sensitivity,fade_ms,loop,bpm,beats,action,original_id,serial,batch_done,batch_total;
    int result;char name[64],original[64];
    SLWave wave;void *wave_mem;SLPeak overview[126],detail[126];
    U view_start,view_span,cache_start,cache_span,zoom,pan,tools,fit,focus,hint,hint_ticks;
    U chop_method,count_index,found_count,pending_analysis,analyzed;
    U applied_method,applied_count,applied_sensitivity;
    U batch_saved,busy_tick,control_tick,a_direction,last_h_tick;
    U load_ref[4],input_source,dialog_return,dialog_refs;
    U undo_valid,undo_count,undo_selected,undo_loop;
    SLRegion undo_regions[64];
    char source_stem[37],pending_stem[37];
    int b_motion,h_motion;
};
enum {CAPTURE=1,REANALYZE=2,PREVIEW=3,SAVE=4,ASSIGN=5,CLEANUP=6,SAVE_ALL=7,SAVE_ALL_POOL=8,LOAD=9};
enum {BUSY=1,READY=2,AUDITIONING=3,SAVED=4,IN_POOL=5,ERROR=6};
enum {AUTO=0,COUNT=1,EVEN=2};
extern int sl_native_stage(const sl_i16 *,U,U,U *,volatile U *);
extern int sl_native_stage_repeat(const sl_i16 *,U,U,U,U *,volatile U *);
extern int sl_native_commit(const char *,U,volatile U *);
extern int sl_native_discard(U);
extern U sl_native_lookup(const char *);
extern int sl_native_reference(U,U *);
extern int sl_native_preview(U);
extern int sl_native_assign_pool(U);
extern int sl_native_read_ref(const U *,sl_i16 **,U *,U *,volatile U *);
extern int sl_browser_reference(void *,int,U *);
extern void sl_adopt(void *,void *,Pair *);
extern int sl_stock_encoder(void *,void *);
extern int sl_stock_key(void *,void *);
extern void sl_stock_draw(void *,Bitmap *);
extern void sl_stock_tick(void *);
extern void sl_stock_destroy(void *);
static Lab *active;
static Lab *pending_cleanup;
static U epoch=1,serial=1;
static U contexts;
/* A linked original may outlive its Lab context. Recorder state writes are the
 * native capture/normalize transition; a full snapshot fingerprint also
 * rejects in-place source changes. PCM is never retained across sessions. */
static volatile U record_epoch=1;
static struct {void *model,*project;U tag,epoch,frames,channels,hash_a,hash_b,id,reference[4];char path[64];} original_cache;
static U ownership_mutex[4];
static int submit(Lab *,U);
static int open_sources(Lab *);
static int open_browser(Lab *);
static U critical(void){N(0x40001608,void (*)(void *))(ownership_mutex);return 0;}
static void restore(U unused){(void)unused;N(0x4000172a,void (*)(void *))(ownership_mutex);}
static void *get(U address){return N(address,void *(*)(void))();}
static void *allocate(U bytes){return N(0x4011ebd6,void *(*)(U))(bytes);}
static void free_mem(void *p){if(p)N(0x4011ebdc,void (*)(void *))(p);}
static void retain(Lab *c){U sr=critical();c->refs++;restore(sr);}
static void release(Lab *c){U sr=critical(),last=--c->refs==0;if(last)contexts--;restore(sr);if(last){free_mem(c->wave_mem);free_mem(c->source);free_mem(c->output);free_mem(c);}}
static int valid(Lab *c){return !c->cancel&&c->epoch==epoch&&get(0x40198b7e)==c->project&&
    N(0x4004aeea,U (*)(void *))(c->project)==c->project_tag;}
static void stop(void){N(0x40139dc2,void (*)(void))();}
static void filename(char *out,const char *prefix,U number){
    char digits[10];U i=0,j=0;
    while(*prefix)out[j++]=*prefix++;
    do{digits[i++]=(char)('0'+number%10);number/=10;}while(number&&i<10);
    while(i<6)digits[i++]='0';
    while(i)out[j++]=digits[--i];
    out[j]=0;
}
/* Keep a bounded basename, removing only the final .wav suffix. Native
 * +Drive samples have no mandatory extension; Transfer appends .wav on export. */
void sl_name_stem(char *out,const char *name){
    U n=0,start=0,i,j=0;
    while(n<191&&name[n]){if(name[n]=='/'||name[n]=='\\')start=n+1;n++;}
    if(n>=4&&name[n-4]=='.'&&(name[n-3]=='w'||name[n-3]=='W')&&
       (name[n-2]=='a'||name[n-2]=='A')&&(name[n-1]=='v'||name[n-1]=='V'))n-=4;
    for(i=start;i<n&&j<36;i++){
        unsigned char x=(unsigned char)name[i];
        out[j++]=((x>='a'&&x<='z')||(x>='A'&&x<='Z')||
                  (x>='0'&&x<='9')||x=='-'||x=='_'||x==' ')?x:'_';
    }
    while(j&&out[j-1]==' ')j--;
    if(!j){out[0]='R';out[1]='E';out[2]='C';j=3;}
    out[j]=0;
}
static int publish(Lab *c,U id,char *name,const char *prefix){
    U i,j,k,n;int rc;char digits[10];
    for(i=0;i<100000;i++){
        if(name==c->original)filename(name,prefix,serial++);
        else {
            const char *stem=c->source_stem[0]?c->source_stem:"REC";
            for(j=0;j<10;j++)name[j]="/recorded/"[j];
            for(k=0;stem[k];k++)name[j++]=stem[k];
            if(c->loop){const char *loop="_LOOP";for(k=0;loop[k];k++)name[j++]=loop[k];}
            name[j++]='_';n=i+1;k=0;
            do{digits[k++]=(char)('0'+n%10);n/=10;}while(n);
            while(k<2)digits[k++]='0';
            while(k)name[j++]=digits[--k];
            name[j]=0;
        }
        rc=sl_native_commit(name,id,&c->cancel);
        if(rc!=-7)return rc;
    }
    return -7;
}
static int discard_id(U *id){
    U tries;int rc;
    if(!*id)return 0;
    for(tries=0;tries<200;tries++){
        rc=sl_native_discard(*id);
        if(!rc){*id=0;return 0;}
        N(0x40136544,void (*)(U))(10);
    }
    return -11;
}
static int discard(Lab *c){
    int rc;stop();rc=discard_id(&c->preview);if(rc)return rc;
    if(c->kept){c->staged=0;c->kept=0;return 0;}
    return discard_id(&c->staged);
}
static int original_folder(void){
    U id=0,offset=0;int flags;
    /* Native mkdir returns -1 for an existing directory too. Accept only a
     * resolved directory (6), never an ordinary file (10) or missing path. */
    N(0x4015746e,int (*)(const char *))("/recorded");
    N(0x400015a0,void (*)(void *))((void *)(sl_uptr)0x46f4deb0);
    flags=N(0x4015a22c,int (*)(const char *,U *,U *,U,U))("/recorded",&id,&offset,0,0);
    N(0x400016d2,void (*)(void *))((void *)(sl_uptr)0x46f4deb0);
    return flags==6?0:-14;
}
static int reusable_original(Lab *c,U frames,U channels,U generation,U hash_a,U hash_b){
    U ref[4],i;
    if(original_cache.epoch!=generation||original_cache.model!=c->model||
       original_cache.project!=c->project||original_cache.tag!=c->project_tag||
       original_cache.frames!=frames||original_cache.channels!=channels||
       original_cache.hash_a!=hash_a||original_cache.hash_b!=hash_b||
       !original_cache.id||sl_native_lookup(original_cache.path)!=original_cache.id||
       sl_native_reference(original_cache.id,ref))return 0;
    for(i=0;i<4;i++)if(ref[i]!=original_cache.reference[i])return 0;
    if(!valid(c)||record_epoch!=generation)return 0;
    c->original_id=original_cache.id;
    for(i=0;i<sizeof(c->original);i++){
        c->original[i]=original_cache.path[i];if(!c->original[i])break;
    }
    return 1;
}
static void remember_original(Lab *c,U frames,U channels,U generation,U hash_a,U hash_b){
    U ref[4],i;
    if(!valid(c)||record_epoch!=generation||
       sl_native_reference(c->original_id,ref))return;
    original_cache.model=c->model;original_cache.project=c->project;
    original_cache.tag=c->project_tag;original_cache.frames=frames;
    original_cache.channels=channels;original_cache.hash_a=hash_a;
    original_cache.hash_b=hash_b;original_cache.id=c->original_id;
    for(i=0;i<sizeof(original_cache.path);i++){
        original_cache.path[i]=c->original[i];if(!c->original[i])break;
    }
    for(i=0;i<4;i++)original_cache.reference[i]=ref[i];
    original_cache.epoch=generation;
}
static int snapshot(Lab *c){
    typedef void (*VOID0)(void);
    U n=N(0x400cd832,U (*)(void))(),ch,at=0,take,i,generation=record_epoch;
    U hash_a=2166136261u,hash_b=0x9e3779b9u,v;sl_i16 *reply;
    int mono=N(0x4004cca2,int (*)(void *))(c->settings)!=0,rc;
    c->capture_stage=1;
    if(!valid(c)||N(0x40015f6c,int (*)(void *))(get(0x40199b66))!=4)return -5;
    if(!n||n>SL_MAX_FRAMES)return -6;
    ch=mono?1:2;c->source=allocate(n*ch*2);if(!c->source)return -12;
    while(at<n){
        if(!valid(c))return -10;
        take=n-at;if(take>0x800/(ch*2))take=0x800/(ch*2);
        N(0x40012542,VOID0)();
        reply=N(0x4001282e,sl_i16 *(*)(U,U,U))(at,take,mono);
        if(reply)for(i=0;i<take*ch;i++){
            v=(U)(unsigned short)reply[i];c->source[at*ch+i]=reply[i];
            hash_a=(hash_a^v)*16777619u;
            hash_b=(hash_b^((v<<16)|v))*2246822519u;
        }
        N(0x40012552,VOID0)();if(!reply)return -8;at+=take;
    }
    if(!valid(c)||n!=N(0x400cd832,U (*)(void))())return -5;
    rc=sl_open(&c->session,c->source,n,ch,48000,c->epoch);if(rc)return rc;
    c->capture_stage=2;rc=original_folder();if(rc)return rc;
    /* Preserve each recording once. Re-entry still takes a fresh owned PCM
     * snapshot; only the previously linked original file write is reused. */
    if(!reusable_original(c,n,ch,generation,hash_a,hash_b)){
        c->capture_stage=3;
        rc=sl_native_stage(c->source,n,ch,&c->original_id,&c->cancel);if(rc)return rc;
        c->capture_stage=4;
        rc=publish(c,c->original_id,c->original,"/recorded/SL-REC-");
        if(rc){sl_native_discard(c->original_id);c->original_id=0;return rc;}
        remember_original(c,n,ch,generation,hash_a,hash_b);
    }
    /* sl_open supplies one complete region. Analyze only when the user exits
     * CHOP settings or explicitly requests it there. */
    c->found_count=1;c->pending_analysis=1;
    c->capture_stage=6;c->wave_mem=allocate(sl_wave_bytes(n));if(!c->wave_mem)return -12;
    rc=sl_wave_build(&c->wave,c->source,n,ch,c->wave_mem,sl_wave_bytes(n),&c->cancel);if(rc)return rc;
    c->view_start=0;c->view_span=n;
    return sl_wave_columns(&c->wave,0,n,c->overview,126);
}
/* Prepare a complete independent source before replacing ANY editor data.
 * Browser cancellation and failed/partial loads leave the old session intact.
 * Neither the source ID nor its reference is ever sent to a writer/remover. */
static int load_sample(Lab *c){
    typedef struct {SLSession session;SLWave wave;sl_i16 *pcm;void *peaks;SLPeak overview[126];} Incoming;
    Incoming *in=allocate(sizeof(*in));U n=0,ch=0,i;int rc;
    if(!in)return -12;
    for(i=0;i<sizeof(*in);i++)((unsigned char *)in)[i]=0;
    rc=sl_native_read_ref(c->load_ref,&in->pcm,&n,&ch,&c->cancel);
    if(!rc&&!valid(c))rc=-10;
    if(!rc)rc=sl_open(&in->session,in->pcm,n,ch,48000,c->epoch);
    if(!rc){in->peaks=allocate(sl_wave_bytes(n));if(!in->peaks)rc=-12;}
    if(!rc)rc=sl_wave_build(&in->wave,in->pcm,n,ch,in->peaks,sl_wave_bytes(n),&c->cancel);
    if(!rc)rc=sl_wave_columns(&in->wave,0,n,in->overview,126);
    if(!rc)rc=original_folder();
    if(!rc&&!valid(c))rc=-10;
    if(!rc)rc=discard(c);
    if(!rc){
        free_mem(c->wave_mem);free_mem(c->source);free_mem(c->output);
        c->session=in->session;c->wave=in->wave;c->source=in->pcm;c->wave_mem=in->peaks;
        for(i=0;i<126;i++)c->overview[i]=in->overview[i];
        in->pcm=0;in->peaks=0;c->output=0;c->output_frames=0;
        c->original_id=0;c->original[0]=0;c->name[0]=0;c->input_source=LOAD;c->undo_valid=0;
        {U i;for(i=0;i<37;i++)c->source_stem[i]=c->pending_stem[i];}
        c->revision++;c->rendered_revision=0;c->found_count=1;c->pending_analysis=1;c->analyzed=0;
        c->view_start=0;c->view_span=n;c->zoom=c->pan=c->tools=c->fit=c->focus=c->cache_span=0;
        c->loop=c->action=c->hint_ticks=0;
    }
    free_mem(in->peaks);free_mem(in->pcm);free_mem(in);return rc;
}
/* Same effective range as render(), without changing processing or markers. */
static int shown_region(Lab *c,SLRegion *r){
    U length;
    if(!c->session.count||c->session.selected>=c->session.count){r->start=r->end=0;return 0;}
    *r=c->session.regions[c->session.selected];
    if(c->loop){
        if(sl_beat_frames(48000,c->bpm*1000,c->beats,&length))return 0;
        r->end=r->start+length;
    }
    return r->start<r->end&&r->end<=c->session.frames&&r->end-r->start>=144;
}
static void center_view(Lab *c,U at){
    U left=at>c->view_span/2?at-c->view_span/2:0;
    if(left>c->session.frames-c->view_span)left=c->session.frames-c->view_span;
    c->view_start=left;
}
static void follow_view(Lab *c,int force){
    SLRegion r;U at;
    if(!c->view_span||!c->session.count)return;
    shown_region(c,&r);if(r.end>c->session.frames)r.end=c->session.frames;
    at=c->focus==1?r.start:c->focus==2?r.end:r.start+(r.end-r.start)/2;
    if(force||at<c->view_start+c->view_span/10||at>c->view_start+c->view_span-c->view_span/10)center_view(c,at);
}
static void zoom_view(Lab *c,int delta){
    SLRegion r;U span=c->view_span,at,level=0,n=c->session.frames,units;
    shown_region(c,&r);if(r.end>c->session.frames)r.end=c->session.frames;
    at=c->focus==1?r.start:c->focus==2?r.end:c->view_start+c->view_span/2;
    units=(U)(delta<0?-delta:delta);
    /* Native delta carries encoder speed. One slow event changes the window
     * by 1/8, medium by 1/4, fast by 1/2; no events are discarded. */
    if(delta>0)span=units>=8?span/2:units>=3?span-span/4:span-span/8;
    else span=units>=8?span*2:units>=3?span+span/3:span+span/7;
    if(span<32)span=32;
    if(span>n)span=n;
    while(level<20&&span<=n/(2u<<level))level++;
    c->zoom=level;c->view_span=span;c->fit=0;center_view(c,at);
}
static void fit_view(Lab *c){
    SLRegion r;U n=c->session.frames,span=n,level=0,length;
    if(c->fit){c->view_start=0;c->view_span=n;c->zoom=0;c->fit=0;return;}
    shown_region(c,&r);if(r.end>n)r.end=n;length=r.end-r.start;
    if(!length)return;
    while(level<20){U next=(n+((1u<<(level+1))-1))>>(level+1);if(next<32||next<length+length/8)break;span=next;level++;}
    c->view_span=span;c->zoom=level;c->focus=0;c->fit=1;center_view(c,r.start+length/2);
}
static int render(Lab *c){
    SLRegion r;U length,fade;int rc;
    if(!c->session.count||c->session.selected>=c->session.count)return -1;
    if(c->staged&&c->rendered_revision==c->revision)return 0;
    rc=discard(c);if(rc)return rc;
    free_mem(c->output);c->output=0;
    r=c->session.regions[c->session.selected];length=r.end-r.start;
    if(c->loop){
        rc=sl_beat_frames(48000,c->bpm*1000,c->beats,&length);if(rc)return rc;
        if(length>c->session.frames-r.start)return -6;
        r.end=r.start+length;
    }
    if(length<144)return -9;
    c->output=allocate(length*c->session.channels*2);if(!c->output)return -12;
    fade=c->fade_ms*48;
    rc=sl_loop(c->source,c->session.frames,c->session.channels,r.start,r.end,fade,c->output,length);
    if(rc)return rc;
    if(!valid(c))return -10;
    rc=sl_native_stage(c->output,length,c->session.channels,&c->staged,&c->cancel);
    if(!rc){c->output_frames=length;c->rendered_revision=c->revision;c->kept=0;}
    return rc;
}
/* UI/worker serialization already excludes editing while busy. Only markers
 * are retained; immutable PCM/file ownership never participates in undo. */
static void remember_grid(Lab *c){
    U i;c->undo_count=c->session.count;c->undo_selected=c->session.selected;
    c->undo_loop=c->loop;
    for(i=0;i<c->session.count;i++)c->undo_regions[i]=c->session.regions[i];
    c->undo_valid=1;
}
static void undo_grid(Lab *c){
    U i;if(!c->undo_valid)return;
    stop();c->session.count=c->undo_count;c->session.selected=c->undo_selected;
    for(i=0;i<c->undo_count;i++)c->session.regions[i]=c->undo_regions[i];
    c->loop=c->undo_loop;c->undo_valid=0;c->revision++;
    c->pending_analysis=1;c->analyzed=0;c->focus=0;c->fit=0;
    c->result=0;c->status=READY;follow_view(c,1);
}
static int save_all(Lab *c,int to_pool){
    U selected=c->session.selected,i;int rc=0;
    if(c->loop||!c->session.count)return -1;
    c->batch_total=c->session.count;c->batch_done=0;c->batch_saved=0;
    for(i=0;i<c->batch_total;i++){
        if(!valid(c)){rc=-10;break;}
        c->session.selected=i;c->revision++;
        rc=render(c);if(rc)break;
        rc=publish(c,c->staged,c->name,"/recorded/SL-CHOP-");
        if(rc)break;
        c->kept=1;c->batch_saved++;
        if(to_pool){
            rc=sl_native_assign_pool(c->staged);if(rc)break;
            c->project_tag=N(0x4004aeea,U (*)(void *))(c->project);
        }
        c->batch_done++;
    }
    c->session.selected=selected;c->revision++;
    if(rc&&c->staged&&!c->kept){int cleanup=discard(c);if(cleanup)return cleanup;}
    return rc;
}
__attribute__((optimize("no-jump-tables"))) static int worker(Fn *fn){
    Lab *c=(Lab *)(sl_uptr)fn->data[0];int rc=0;U op=c->operation;
    if(op==CLEANUP)rc=discard(c);
    else if(!valid(c))rc=-10;
    else if(op==CAPTURE)rc=snapshot(c);
    else if(op==LOAD)rc=load_sample(c);
    else if(op==REANALYZE){
        SLSession previous=c->session;
        if(c->chop_method==COUNT)rc=sl_analyze_count(&c->session,c->epoch,c->count_index,&c->found_count);
        else if(c->chop_method==EVEN)rc=sl_analyze_even(&c->session,c->epoch,c->count_index);
        else rc=sl_analyze(&c->session,c->epoch,c->sensitivity,35);
        if(!rc){SLSession next=c->session;c->session=previous;remember_grid(c);c->session=next;
            c->analyzed=1;c->found_count=c->chop_method==COUNT?c->found_count:c->session.count;
            c->applied_method=c->chop_method;c->applied_count=c->count_index;
            c->applied_sensitivity=c->sensitivity;c->focus=0;follow_view(c,1);}
        else c->session=previous;
    }
    else if(op==SAVE_ALL||op==SAVE_ALL_POOL)rc=save_all(c,op==SAVE_ALL_POOL);
    else {
        /* Reject an unpreviewable region before allocating and staging its
         * processed copy; SAVE can still use the full recording. */
        if(op==PREVIEW){SLRegion r;
            if(shown_region(c,&r)&&r.end-r.start>0x1fffa0/(c->session.channels*2))rc=-13;
        }
        if(!rc)rc=render(c);
        if(!rc&&!valid(c))rc=-10;
        /* Native dedicated preview loader has a fixed 0x1fffa0-byte limit.
         * Short loops preview two exact cycles to expose the seam. A longer
         * loop that fits once uses the proven single-record preview route. */
        if(!rc&&op==PREVIEW){
            U one_bytes=c->output_frames*c->session.channels*2;
            if(one_bytes>0x1fffa0)rc=-13;
            else if(c->loop&&one_bytes*2<=0x1fffa0){
                if(!c->preview)rc=sl_native_stage_repeat(c->output,c->output_frames,c->session.channels,2,&c->preview,&c->cancel);
                if(!rc)rc=sl_native_preview(c->preview);
            }else rc=sl_native_preview(c->staged);
        }
        if(!rc&&(op==SAVE||op==ASSIGN)&&!c->kept){
            rc=publish(c,c->staged,c->name,c->loop?"/recorded/SL-LOOP-":"/recorded/SL-CHOP-");
            if(!rc)c->kept=1;
        }
        if(!rc&&op==ASSIGN)rc=sl_native_assign_pool(c->staged);
    }
    if(c->cancel){stop();if(!rc)rc=-10;if(c->preview||(c->staged&&!c->kept))discard(c);}
    /* The queued job reports load/start success, not audio completion. Actual
     * playback state belongs to DSP feedback and is observed by the UI tick. */
    c->result=rc;c->status=rc?ERROR:op==SAVE||op==SAVE_ALL?SAVED:op==ASSIGN||op==SAVE_ALL_POOL?IN_POOL:READY;
    c->busy=0;
    if(c->cancel&&(c->preview||(c->staged&&!c->kept))){
        free_mem(c->source);c->source=0;free_mem(c->output);c->output=0;sl_invalidate(&c->session);
        if(!submit(c,CLEANUP)&&!pending_cleanup){retain(c);pending_cleanup=c;}
    }
    if(!rc&&op==ASSIGN)c->project_tag=N(0x4004aeea,U (*)(void *))(c->project);
    return rc;
}
static int manager(Fn *to,const Fn *from,int op){
    if(op==2){to->data[0]=from->data[0];to->data[1]=from->data[1];retain((Lab *)(sl_uptr)from->data[0]);}
    if(op==3)release((Lab *)(sl_uptr)to->data[0]);
    return 0;
}
static int submit(Lab *c,U operation){
    typedef unsigned char (*QUEUE)(void *,const char *,Fn *,Fn *);
    Fn work={{(U)(sl_uptr)c,0},(U)(sl_uptr)manager,(U)(sl_uptr)worker},done={{0,0},0,0};
    int ok;if(operation==SAVE_ALL||operation==SAVE_ALL_POOL){c->batch_total=c->session.count;c->batch_done=0;c->batch_saved=0;}
    c->operation=operation;c->busy=1;c->status=BUSY;retain(c);
    ok=N(0x400f0c1c,QUEUE)(get(0x40198cf2),"Sample Lab",&work,&done);release(c);
    if(!ok){c->busy=0;c->result=-8;c->status=ERROR;}return ok;
}
static void commit_analysis(Lab *c){
    if(c->pending_analysis&&!c->loop&&!c->busy&&valid(c)){
        c->pending_analysis=0;c->result=0;submit(c,REANALYZE);
    }
}
static void update_pending(Lab *c){
    c->pending_analysis=!c->analyzed||c->chop_method!=c->applied_method||
        (c->chop_method==AUTO?c->sensitivity!=c->applied_sensitivity:
         c->count_index!=c->applied_count);
}
static int create_lab(void *view,int capture){
    Lab *c;U i;
    if(active)return 0;
    if(pending_cleanup){c=pending_cleanup;if(submit(c,CLEANUP)){pending_cleanup=0;release(c);}return 0;}
    if(capture&&N(0x40015f6c,int (*)(void *))(get(0x40199b66))!=4)return 0;
    c=allocate(sizeof(*c));if(!c)return 0;
    for(i=0;i<sizeof(*c);i++)((unsigned char *)c)[i]=0;
    c->refs=1;contexts++;c->view=view;c->project=get(0x40198b7e);
    c->settings=*(void **)((unsigned char *)view+0xa0);
    c->model=*(void **)((unsigned char *)get(0x40199b66)+0x10);
    c->project_tag=N(0x4004aeea,U (*)(void *))(c->project);c->epoch=++epoch;
    c->sensitivity=96;c->fade_ms=0;c->bpm=120;c->beats=4;c->count_index=8;c->revision=1;
    c->a_direction=0;c->last_h_tick=(U)-3;
    active=c;c->input_source=CAPTURE;c->status=READY;
    if(capture)submit(c,CAPTURE);
    N(0x4011b126,void (*)(void *))(view);return 1;
}
int sl_lab_open(void *view){return create_lab(view,1);}
void sl_lab_close(void *view){
    Lab *c=active;U sr;
    if(!c||c->view!=view)return;
    sr=critical();active=0;c->view=0;c->cancel=1;epoch++;restore(sr);stop();
    if(!c->busy&&(c->preview||(c->staged&&!c->kept))&&!submit(c,CLEANUP)&&!pending_cleanup){retain(c);pending_cleanup=c;}
    release(c);
}
/* Native SOURCE menu + SamplePicker, not a second browser/editor. Native
 * std::function copies retain the Lab, so late callbacks cannot use freed UI.
 * Closing SamplerView cancels its Lab; outstanding child callbacks then no-op.
 */
static void retain_dialog(Lab *c){U sr=critical();c->refs++;c->dialog_refs++;restore(sr);}
static int source_manager(Fn *to,const Fn *from,int op){
    U sr;Lab *c;
    if(op==2){to->data[0]=from->data[0];to->data[1]=from->data[1];c=(Lab *)(sl_uptr)from->data[0];
        retain_dialog(c);}
    if(op==3){c=(Lab *)(sl_uptr)to->data[0];sr=critical();c->dialog_refs--;restore(sr);release(c);}
    return 0;
}
static void close_child(void *view){U *vt=*(U **)view;((void (*)(void *))(sl_uptr)vt[10])(view);}
static void show_child(Lab *c,void *child){
    /* SamplerView IS the View base. Menu's +0x64 is View(+56)+0x2c;
     * copying the Menu object offset here would read an unrelated zero. */
    Pair owned;void *parent=*(void **)((unsigned char *)c->view+0x2c);
    c->dialog_return=1;
    sl_adopt(parent,child,&owned);
    N(0x4011c08a,int (*)(void *,Pair *,U))(parent,&owned,0);
    N(0x40197d26,void (*)(void *))(&owned.control);
}
static void selected(Fn *fn,int index,Pair *directory,Pair *origin){
    Lab *c=(Lab *)(sl_uptr)fn->data[0];U ref[4],i;
    if(!valid(c)||c->busy||index<0||!directory||!directory->raw)return;
    if(sl_browser_reference(directory->raw,index,ref)||ref[0]==0xffffffffu){c->result=-5;c->status=ERROR;return;}
    {
        /* Same RTTI/bounds/entry contract as stock40159a50 and filename
         * accessor40159592: entry vector104/108, inline C name at entry+14. */
        U *dir=N(0x401e317c,U *(*)(void *,void *,void *,int))(directory->raw,(void *)0x40225c24,(void *)0x40225cec,0);
        U *entry=0;
        if(dir&&dir[0x108/4]>=dir[0x104/4]&&(U)index<(dir[0x108/4]-dir[0x104/4])/8)
            entry=*(U **)(sl_uptr)(dir[0x104/4]+(U)index*8);
        sl_name_stem(c->pending_stem,entry?(const char *)entry+0x14:"");
    }
    for(i=0;i<4;i++)c->load_ref[i]=ref[i];
    c->result=0;submit(c,LOAD);
    /* The native invoker owns this origin for the duration of the callback.
     * Never save its weak pointer or the Directory/entry beyond this call. */
    if(origin&&origin->raw)close_child(origin->raw);
}
static int open_browser(Lab *c){
    void *picker,*view;Fn fn,empty={{0,0},0,0};
    if(!valid(c)||c->busy)return 0;
    picker=allocate(0x4f4);if(!picker){c->result=-12;c->status=ERROR;return 0;}
    /* Same native SampleManager base + filesystem directory used by
     * SamplePicker4001d80e. Reuse its read-only selection configuration,
     * omitting the derived Pool-slot callback and ASSIGN SAMPLE header. */
    N(0x40029e7a,void (*)(void *,int,int,Fn *,int))(picker,0,1,&empty,1);
    view=(unsigned char *)picker+56;
    N(0x4011b51a,void (*)(void *,U))(view,5);
    N(0x4011afbc,void (*)(void *,U))(view,1);
    N(0x400901f6,void (*)(void *,U))(picker,1);
    N(0x40114d40,void (*)(void *))(picker);
    N(0x4009020e,void (*)(void *,U))(picker,0);
    ((unsigned char *)picker)[0x4f1]=1;
    fn.data[0]=(U)(sl_uptr)c;fn.data[1]=0;fn.manager=(U)(sl_uptr)source_manager;fn.invoke=(U)(sl_uptr)selected;retain_dialog(c);
    N(0x400283ca,void (*)(void *,Fn *))(picker,&fn);
    N(0x40197ce0,void (*)(void *))(&fn);
    show_child(c,view);return 1;
}
static void source_choice(Fn *fn){
    Lab *c=(Lab *)(sl_uptr)fn->data[0];U record=fn->data[1]&1;void *view;
    if(!valid(c)||c->busy)return;
    close_child((void *)(sl_uptr)(fn->data[1]&~1u));
    if(!record){open_browser(c);return;}
    if(N(0x40015f6c,int (*)(void *))(get(0x40199b66))!=4){c->result=-15;c->status=ERROR;return;}
    view=c->view;sl_lab_close(view);sl_lab_open(view);
    if(active)active->dialog_return=1;
}
/* Same native Menu view vtable/draw override as the established Credits view;
 * selection, callbacks, destruction and NO navigation remain native. */
static void source_draw(void *view,Bitmap *b){
    void *menu=(unsigned char *)view-56;U *vt=*(U **)menu;
    int selected=((int (*)(void *))(sl_uptr)vt[9])(menu);
    void *font=(void *)(sl_uptr)0x44d2e6f4;
    typedef int (*TEXT)(Bitmap *,void *,int,int,int,const char *,...);
    TEXT text=N(0x40113dc8,TEXT);
    N(0x401131ae,void (*)(Bitmap *,int,int,int,int,int))(b,0,0,127,63,0);
    text(b,font,3,56,0,"SAMPLE LAB / SOURCE");
    text(b,font,4,selected==1?29:41,0,">");
    text(b,font,13,41,0,"RECORD (TRIM)");
    text(b,font,13,29,0,"LOAD FROM +DRIVE");
    text(b,font,3,14,0,"RECORD: STOP AT TRIM");
    text(b,font,3,3,0,"UP/DOWN  YES  NO:BACK");
}
static int open_sources(Lab *c){
    void *menu,*item;U title=0,i,*vt;char allocator=0;Fn fn,empty={{0,0},0,0};static const Bitmap blank;
    if(!valid(c)||c->busy)return 0;
    menu=allocate(508);if(!menu)return 0;
    N(0x401e7e7a,void (*)(void *,const char *,void *))(&title,"SAMPLE LAB SOURCE",&allocator);
    N(0x40119f6c,void (*)(void *,const char *,int,void *,void *,int))(menu,"SampleLabSource",1,&title,0,1);
    N(0x401e63ca,void (*)(void *))(&title);
    vt=(U *)((unsigned char *)menu+428);
    for(i=0;i<20;i++)vt[i]=((U *)(sl_uptr)0x4021dd90)[i];
    vt[6]=(U)(sl_uptr)source_draw;*(U *)((unsigned char *)menu+56)=(U)(sl_uptr)(vt+2);
    for(i=0;i<2;i++){
        item=allocate(84);if(!item)break;
        fn.data[0]=(U)(sl_uptr)c;fn.data[1]=((U)(sl_uptr)menu+56)|(i==0);fn.manager=(U)(sl_uptr)source_manager;fn.invoke=(U)(sl_uptr)source_choice;retain_dialog(c);
        N(0x40115b90,void (*)(void *,const char *,Fn *,const Bitmap *,Fn *))(item,i==0?"RECORD (TRIM)":"LOAD FROM +DRIVE",&fn,&blank,&empty);
        N(0x40197ce0,void (*)(void *))(&fn);
        N(0x4011527a,void (*)(void *,void *))(menu,item);
    }
    show_child(c,(unsigned char *)menu+56);return 1;
}
void sl_lab_record_state(void *model,U state){
    Lab *c;U sr;
    if(!active){
        *(U *)((unsigned char *)model+0xa4)=state;
        if(++record_epoch==0)record_epoch=1;
        return;
    }
    sr=critical();*(U *)((unsigned char *)model+0xa4)=state;
    if(++record_epoch==0)record_epoch=1;
    c=active;if(c&&c->model==model){epoch++;c->cancel=1;}restore(sr);
}
void sl_lab_destroy(void *view){sl_lab_close(view);sl_stock_destroy(view);}
void sl_lab_tick(void *view){
    Lab *c;
    sl_stock_tick(view);
    c=active;
    if(c&&c->view==view){
        /* The recorder setter can run inside native capture/finalization.
         * Retire the cancelled Lab here on the UI task, before its modal
         * handlers can hide later native Recording/normalization events. */
        if(c->cancel){sl_lab_close(view);N(0x4011b126,void (*)(void *))(view);return;}
        c->control_tick++;
        if(c->busy)c->busy_tick++;
        if(c->hint_ticks)c->hint_ticks--;
        /* Native browser uses this same preview-active getter (PDM +0x18).
         * Boolean return is byte-wide; upper D0 bits are not defined. A
         * delayed start or a very short preview must never latch the UI. */
        if(!c->busy&&!c->result&&c->operation==PREVIEW&&valid(c))
            c->status=N(0x400cd8e6,unsigned char (*)(void))()?AUDITIONING:READY;
        N(0x4011b126,void (*)(void *))(view);
    }
}
static U clamp(int value,U lo,U hi){return value<(int)lo?lo:value>(int)hi?hi:(U)value;}
int sl_lab_encoder(void *view,void *event){
    Lab *c=active;int knob=N(0x40111a3a,int (*)(void *))(event),delta;
    SLRegion *r;U amount;
    if(!c||c->view!=view){
        if(knob==0&&N(0x40111ac8,unsigned char (*)(void *))(event))return sl_lab_open(view);
        return sl_stock_encoder(view,event);
    }
    if(c->cancel){sl_lab_close(view);return sl_stock_encoder(view,event);}
    if(knob<0)return sl_stock_encoder(view,event);
    if(c->busy||c->dialog_refs||!valid(c))return 1;
    if(c->operation==CAPTURE&&c->result)return 1;
    /* Info is read-only; its PUSH shortcuts remain in sl_lab_key. */
    if(c->tools==2)return 1;
    delta=N(0x40111aaa,int (*)(void *,int,int))(event,1,1);if(!delta)return 1;
    if(delta>64)delta=64;
    if(delta< -64)delta= -64;
    /* Native events already contain acceleration. Like the existing FUS2
     * discrete selectors, bound the final increment, not the shared input. */
    if(knob==7){
        if(c->control_tick-c->last_h_tick<3)return 1;
        c->last_h_tick=c->control_tick;
        if((c->h_motion>0)!=(delta>0))c->h_motion=0;
        c->h_motion+=delta>0?1:-1;
        if(c->h_motion<2&&c->h_motion> -2)return 1;
        c->h_motion=0;
        c->action=clamp((int)c->action+(delta>0?1:-1),0,c->loop?2:4);c->hint_ticks=0;return 1;
    }
    if(knob==6&&!(c->tools&&c->loop)){
        if(c->pan&&!c->tools){int move=delta*(int)(c->view_span/8?c->view_span/8:1);
            c->view_start=clamp((int)c->view_start+move,0,c->session.frames-c->view_span);}
        else zoom_view(c,delta);
        c->hint=7;c->hint_ticks=45;return 1;
    }
    if(knob==1&&!c->loop&&c->tools){U before;c->hint=1;c->hint_ticks=45;
            int divisor=c->chop_method==AUTO?4:6,change;
            if((c->b_motion>0)!=(delta>0))c->b_motion=0;
            c->b_motion+=delta;change=c->b_motion/divisor;c->b_motion-=change*divisor;
            if(!change)return 1;
            before=c->chop_method==AUTO?c->sensitivity:c->count_index;
            if(c->chop_method==AUTO)c->sensitivity=clamp((int)c->sensitivity+change,1,127);
            else c->count_index=clamp((int)c->count_index+change,1,64);
            if(before==(c->chop_method==AUTO?c->sensitivity:c->count_index))return 1;
            c->revision++;c->result=0;
            if(c->tools)update_pending(c);else submit(c,REANALYZE);
            return 1;
    }
    if(knob==3){U was=c->loop;
        if(!c->tools||N(0x40111ac8,unsigned char (*)(void *))(event))return 1;
        c->loop=clamp((int)c->loop+(delta>0?1:-1),0,1);if(c->loop&&c->action>2)c->action=2;
        c->b_motion=0;
        if(c->loop)c->result=0;
        else if(was&&c->pending_analysis&&!c->tools){commit_analysis(c);return 1;}}
    if(!c->session.count)return 1;
    if(knob==0){
        /* Four consistent motion events per adjacent selection. Magnitude
         * cannot skip a region; reversal discards the old partial gesture.
         * Aggregated accelerated magnitude cannot skip a region. Direction
         * reversal starts a fresh pair rather than accumulating stale motion. */
        U direction=delta>0?1:2;
        if((c->a_direction&3)!=direction)c->a_direction=direction;
        c->a_direction+=4;
        if((c->a_direction>>2)<4)return 1;
        c->a_direction=0;
        c->session.selected=clamp((int)c->session.selected+(delta>0?1:-1),0,c->session.count-1);
        c->focus=0;c->hint_ticks=0;follow_view(c,1);
    }
    if(knob==2)c->fade_ms=clamp((int)c->fade_ms+delta,0,50);
    if(knob==6){c->beats=clamp((int)c->beats+delta,1,32);c->focus=2;follow_view(c,0);}
    r=&c->session.regions[c->session.selected];
    if(knob==1&&!c->loop&&!c->tools){
        U len=r->end-r->start,lo=0,hi=c->session.frames-len,next;
        int step=delta>=8||delta<=-8?9600:delta>=3||delta<=-3?2400:480;
        /* Regions are independent audio windows. Keep their start markers in
         * order while allowing overlap; never modify a neighboring region. */
        if(c->session.selected)lo=c->session.regions[c->session.selected-1].start+1;
        if(c->session.selected+1<c->session.count){
            U boundary=c->session.regions[c->session.selected+1].start;
            if(!boundary)return 1;
            if(boundary-1<hi)hi=boundary-1;
        }
        if(lo>hi||r->start<lo||r->start>hi)return 1;
        next=clamp((int)r->start+delta*step,lo,hi);
        if(next==r->start)return 1;
        r->start=next;r->end=next+len;c->focus=0;c->hint_ticks=0;
        follow_view(c,1);c->revision++;
        if(c->status==SAVED||c->status==IN_POOL)c->status=READY;
        return 1;
    }
    if(knob==1&&c->loop){r->start=clamp((int)r->start+delta,0,c->session.frames-144);
        c->focus=1;follow_view(c,0);}
    /* Use actual visible span, not the approximate zoom-level buckets.
     * One native unit is about1/4 waveform pixel at wide views; the48-frame
     * floor preserves deep-zoom feel. Native delta stays linear, with no
     * magnitude tiers or history. Held rotation remains one frame. */
    amount=N(0x40111ac8,unsigned char (*)(void *))(event)?0:
        (c->view_span>48u*512u?c->view_span/512u:48u);
    if(knob==4)r->start=clamp((int)r->start+(amount?delta*(int)amount:delta>0?1:-1),0,r->end>=144?r->end-144:0);
    if(knob==5&&!c->loop)r->end=clamp((int)r->end+(amount?delta*(int)amount:delta>0?1:-1),r->start+144,c->session.frames);
    if(knob==5&&c->loop){c->bpm=clamp((int)c->bpm-delta,20,300);
        c->focus=2;follow_view(c,1);}
    if(knob==4||(knob==5&&!c->loop)){c->focus=knob==4?1:2;follow_view(c,0);}
    if(knob>=1&&knob<=6){c->hint=knob;c->hint_ticks=45;}
    c->revision++;
    if(c->status==SAVED||c->status==IN_POOL)c->status=READY;
    return 1;
}
int sl_lab_key(void *view,void *event){
    Lab *c=active;int key,down;
    key=N(0x40114984,int (*)(void *))(event);
    down=N(0x401149ac,int (*)(void *))(event);
    if(!c||c->view!=view){
        /* Native encoder pushes are key codes41..48, separate from rotation.
         * Stock SamplerView consumes them without a trim action. Keep every
         * other key/state on its stock route; no hold/rotation is required. */
        if(key==41&&down&&N(0x40015f6c,int (*)(void *))(get(0x40199b66))==4){
            sl_lab_open(view);return 1;
        }
        if(key==42&&down){U state=N(0x40015f6c,U (*)(void *))(get(0x40199b66));
            if((state==0||state==4)&&create_lab(view,0)){open_browser(active);return 1;}}
        return sl_stock_key(view,event);
    }
    if(c->cancel){sl_lab_close(view);return sl_stock_key(view,event);}
    if(c->dialog_refs)return 1;
    /* Choosers close on press, Lab acts on release. Never reinterpret their
     * YES/NO release as PLAY/SAVE or closing the editor. A new press clears
     * the guard if the chooser consumed its own release. */
    if(c->dialog_return){
        if(down&&(key==10||key==12)){c->dialog_return=0;return 1;}
        if(N(0x401149dc,int (*)(void *))(event)&&
           !(N(0x4011498e,U (*)(void *))(event)&8))c->dialog_return=0;
    }
    if(key==12&&down){
        if(c->tools){c->tools=0;c->hint_ticks=0;c->a_direction=0;c->b_motion=c->h_motion=0;N(0x4011b126,void (*)(void *))(view);return 1;}
        sl_lab_close(view);N(0x4011b126,void (*)(void *))(view);return 1;
    }
    if(key==44&&down&&!c->busy&&valid(c)){c->a_direction=0;c->b_motion=c->h_motion=0;open_sources(c);return 1;}
    if(down&&!c->busy&&valid(c)&&c->wave.source){
        if(key==46){if(!(N(0x4011498e,U (*)(void *))(event)&8)){U had=c->undo_valid;if(had)c->tools=0;undo_grid(c);c->a_direction=0;c->b_motion=c->h_motion=0;c->hint=had?10:11;c->hint_ticks=120;}return 1;}
        if(key==41){fit_view(c);c->pan=0;c->tools=0;c->hint_ticks=0;c->a_direction=0;c->b_motion=c->h_motion=0;return 1;}
        if(key==42&&c->tools==1&&!c->loop){c->chop_method=(c->chop_method+1)%3;
            c->b_motion=0;c->result=0;c->revision++;update_pending(c);return 1;}
        if(key==47){c->pan=c->tools?1:!c->pan;c->tools=0;c->hint_ticks=0;c->a_direction=0;c->b_motion=c->h_motion=0;return 1;}
        if(key==48){U previous=c->tools;c->tools=previous==0?1:previous==1?2:0;
            c->hint_ticks=0;c->a_direction=0;c->b_motion=c->h_motion=0;if(previous==1)commit_analysis(c);return 1;}
    }
    if(key==10&&down&&c->pending_analysis&&c->tools&&!c->loop){c->a_direction=0;commit_analysis(c);return 1;}
    if(key==10&&down&&!c->busy&&valid(c)&&c->session.count&&!(c->operation==REANALYZE&&c->result)&&!(c->operation==CAPTURE&&c->result))submit(c,c->action==0?PREVIEW:c->action==1?SAVE:c->action==2?ASSIGN:c->action==3?SAVE_ALL:SAVE_ALL_POOL);
    return 1;
}
static void box(Bitmap *b,int x,int y,int right,int top,int color){
    N(0x401131ae,void (*)(Bitmap *,int,int,int,int,int))(b,x,y,right,top,color);
}
static int wave_x(U frame,U left,U span){
    if(frame<=left)return 1;
    if(frame>=left+span)return 126;
    return 1+(int)((frame-left)*125/span);
}
static int fade_region(Lab *c,SLRegion r,SLRegion *f){
    U n=c->fade_ms*48,length=r.end-r.start;
    f->start=f->end=0;if(!n)return 0;
    if(n<2||n>length/2||n>32768)return -1;
    if(r.start>=n){f->start=r.end-n;f->end=r.end;}
    else if(r.end<=c->session.frames&&c->session.frames-r.end>=n){f->start=r.start;f->end=r.start+n;}
    else return -1;
    return 1;
}
static void draw_wave(Lab *c,Bitmap *b,SLRegion r,int range_ok){
    U i,j,pos,peak=c->wave.peak?c->wave.peak:1;
    int lo,hi,x,s=wave_x(r.start,c->view_start,c->view_span),e=wave_x(r.end,c->view_start,c->view_span),last=-1,fa;
    SLRegion fade;SLPeak p;
    if(c->cache_start!=c->view_start||c->cache_span!=c->view_span){
        if(sl_wave_columns(&c->wave,c->view_start,c->view_span,c->detail,126))return;
        c->cache_start=c->view_start;c->cache_span=c->view_span;
    }
    /* Entire immutable recording above; the bracket is the visible window. */
    for(i=0;i<126;i++){
        p=c->overview[i];lo=51+(int)p.low*2/(int)peak;hi=51+(int)p.high*2/(int)peak;
        box(b,(int)i+1,lo,(int)i+1,hi,1);
    }
    x=wave_x(c->view_start,0,c->session.frames);j=(U)wave_x(c->view_start+c->view_span,0,c->session.frames);
    box(b,x,49,(int)j,49,1);box(b,x,49,x,53,1);box(b,(int)j,49,(int)j,53,1);
    if(c->session.count&&r.start<c->view_start+c->view_span&&r.end>c->view_start){
        if(e<s)e=s;
        box(b,s,16,e,45,1);
    }else{s=128;e=128;}
    for(i=0;i<126;i++){
        p=c->detail[i];lo=31+(int)p.low*13/(int)peak;hi=31+(int)p.high*13/(int)peak;
        x=(int)i+1;box(b,x,lo,x,hi,x>=s&&x<=e?0:1);
    }
    /* Dense boundary markers stay above the waveform, one marker per pixel. */
    for(i=0;i<c->session.count;i++){
        pos=c->session.regions[i].start;
        if(pos<c->view_start||pos>c->view_start+c->view_span)continue;
        x=wave_x(pos,c->view_start,c->view_span);if(x!=last)box(b,x,46,x,48,1);last=x;
    }
    if(s<128){
        box(b,s,16,s,45,0);box(b,e,16,e,45,0);
        for(i=0;i<3;i++){box(b,s-(int)i,48-(int)i,s+(int)i,48-(int)i,1);box(b,e-(int)i,14+(int)i,e+(int)i,14+(int)i,1);}
    }
    fa=fade_region(c,r,&fade);
    if(range_ok&&fa>0){
        for(i=0;i<126;i++){
            pos=c->view_start+c->view_span*i/126;
            if(pos>=fade.start&&pos<fade.end)box(b,(int)i+1,17,(int)i+1,17+(int)(i%3),0);
        }
    }
}
/* Keep hint selection as code: executable-section ISA checking must not decode
 * a compiler-generated inline jump table as instructions. */
__attribute__((optimize("no-jump-tables"))) void sl_lab_draw(void *view,Bitmap *b){
    typedef int (*TEXT)(Bitmap *,void *,int,int,int,const char *,...);
    TEXT text=N(0x40113dc8,TEXT);void *font=(void *)(sl_uptr)0x44d2e70c;
    Lab *c=active;SLRegion r={0,0};const char *action,*short_action,*mode,*yes_action;int range_ok=0,can_draw,message_y,show_action=0;
    if(!c||c->view!=view){
        sl_stock_draw(view,b);
        {U state=N(0x40015f6c,U (*)(void *))(get(0x40199b66));if(state==0){
            /* Blank band below the header, above the native THRES value. */
            N(0x401131ae,void (*)(Bitmap *,int,int,int,int,int))(b,98,45,127,52,0);
            text(b,(void *)(sl_uptr)0x44d2e6f4,100,46,0,"B:LOAD");
        }else if(state==4){
            /* A push opens Lab in stopped TRIM; keep Y:SAVE/Fn+D:DEL and
             * the adjacent Fn+H shortcut exactly native. */
            N(0x401131ae,void (*)(Bitmap *,int,int,int,int,int))(b,101,55,127,63,0);
            text(b,(void *)(sl_uptr)0x44d2e6f4,104,56,0,"A:LAB");
        }}
        return;
    }
    if(c->cancel){sl_stock_draw(view,b);return;}
    /* Stock TRIM's narrower native font, only inside the active Lab view. */
    font=(void *)(sl_uptr)0x44d2e6f4;
    box(b,0,0,127,63,0);action=c->action==0?"PLAY":c->action==1?"SAVE":c->action==2?"POOL":c->action==3?"SAVE ALL":"ALL POOL";
    short_action=c->action==3?"SAVEALL":c->action==4?"ALLPOOL":action;
    mode=!c->analyzed?"WHOLE":c->chop_method==AUTO?"AUTO":c->chop_method==COUNT?"COUNT":"EVEN";
    message_y=c->tools?40:8;
    can_draw=valid(c)&&c->wave.source&&!(c->busy&&(c->operation==CAPTURE||c->operation==REANALYZE||c->operation==LOAD));
    if(can_draw)range_ok=shown_region(c,&r);
    yes_action=c->pending_analysis&&!c->loop?"ANALYZE":action;
    if(c->tools==2){
        text(b,font,2,56,0,"SAMPLE LAB / INFO");
        text(b,font,2,48,0,"PUSH A:FIT B:TYPE(SETTINGS)");
        text(b,font,2,40,0,"PUSH D:SOURCE F:UNDO GRID");
        text(b,font,2,32,0,"PUSH G:PAN H:WAVE");
        text(b,font,2,24,0,"SETTINGS H:APPLY + INFO");
        text(b,font,2,16,0,"NO:BACK TO WAVE / AGAIN:EXIT");
        text(b,font,2,8,0,c->loop?"HOLD E:1fr F:BPM SET G:BEATS":"HOLD E/F:1fr C:FADE ms");
        if(c->busy)text(b,font,2,0,0,"WORKING / NO:WAVE");
        else if(c->result)text(b,font,2,0,0,"ERROR %d / NO:WAVE",c->result);
        else if(c->hint_ticks&&c->hint==11)text(b,font,2,0,0,"NO MARKER UNDO / NO:WAVE");
        else text(b,font,2,0,0,"YES:%s / H:ACTION(WAVE)",yes_action);
        return;
    }
    if(!c->wave.source&&!c->busy){
        text(b,font,98,56,0,"D:SRC");text(b,font,2,36,0,"SAMPLE LAB / SOURCE");
        text(b,font,2,24,0,"RECORD / LOAD +DRIVE");
    }else if(c->tools){
        /* Four 32-pixel columns: physical A-D above, E-H below. */
        text(b,font,2,56,0,"A:SEL");text(b,font,34,56,0,"B:%s",c->loop?"FINE":c->chop_method==AUTO?"AUTO":c->chop_method==COUNT?"COUNT":"EVEN");
        text(b,font,66,56,0,"C:FADE");text(b,font,98,56,0,"D:MODE");
        text(b,font,2,48,0,"%02u/%02u",c->session.count?c->session.selected+1:0,c->session.count);
        if(c->loop)text(b,font,34,48,0,"1fr");
        else if(c->chop_method==AUTO)text(b,font,34,48,0,"S%u",c->sensitivity);
        else text(b,font,34,48,0,"%u",c->count_index);
        text(b,font,66,48,0,"%ums",c->fade_ms);text(b,font,98,48,0,"%s",c->loop?"LOOP":"CHOP");
        if(!c->busy&&!c->result){
            int width=text(b,(void *)(sl_uptr)0x44d2e70c,2,30,0,"YES: %s",yes_action);
            box(b,2,28,width>124?125:width,28,1);
        }
        text(b,font,2,19,0,c->pending_analysis&&!c->loop?"H:APPLY+INFO  NO:WAVE":"H:INFO  NO:WAVE");
        text(b,font,2,0,0,"E:START");text(b,font,34,0,0,c->loop?"F:BPM":"F:END");
        text(b,font,66,0,0,c->loop?"G:BEAT":"G:ZOOM");text(b,font,98,0,0,"H:INFO");
        /* Compact large frame values only here; trim hints retain full frames. */
        if(c->session.count)r=c->session.regions[c->session.selected];
        text(b,font,2,8,0,r.start<1000000?"%uf":"%us",r.start<1000000?r.start:r.start/48000);
        if(c->loop)text(b,font,34,8,0,"%u",c->bpm);
        else text(b,font,34,8,0,r.end<1000000?"%uf":"%us",r.end<1000000?r.end:r.end/48000);
        if(c->loop)text(b,font,66,8,0,"%u",c->beats);
        else text(b,font,66,8,0,c->view_span<1000000?"%uf":"%us",c->view_span<1000000?c->view_span:c->view_span/48000);
        text(b,font,98,8,0,"%s",short_action);
    }else{
        if(can_draw)draw_wave(c,b,r,range_ok);
        text(b,font,2,56,0,"A:SEL");text(b,font,34,56,0,c->loop?"B:FINE":"B:MOVE");
        text(b,font,66,56,0,"C:FADE");text(b,font,98,56,0,"D:SRC");
        box(b,0,8,127,14,0);
        text(b,font,2,0,0,"E:START");text(b,font,34,0,0,c->loop?"F:BPM":"F:END");
        text(b,font,66,0,0,c->pan?"G:PAN":"G:ZOOM");text(b,font,98,0,0,"H:MENU");
    }
    if((c->operation==SAVE_ALL||c->operation==SAVE_ALL_POOL)&&c->busy)
        text(b,font,2,message_y,0,c->operation==SAVE_ALL?"SAVE %u/%u NO:CANCEL":"POOL %u/%u NO:CANCEL",c->batch_done,c->batch_total);
    else if((c->operation==SAVE_ALL||c->operation==SAVE_ALL_POOL)&&c->result)
        text(b,font,2,message_y,0,"STOP %u/%u FILES %u",c->batch_done,c->batch_total,c->batch_saved);
    else if(c->operation==SAVE_ALL&&c->status==SAVED)text(b,font,2,message_y,0,"SAVED ALL %u/%u NO:BACK",c->batch_done,c->batch_total);
    else if(c->operation==SAVE_ALL_POOL&&c->status==IN_POOL)text(b,font,2,message_y,0,"POOLED %u/%u NO:BACK",c->batch_done,c->batch_total);
    else if(c->busy&&c->operation==CAPTURE){
        const char *dots=(c->busy_tick/8)%3==0?".":(c->busy_tick/8)%3==1?"..":"...";
        const char *phase=c->capture_stage<=1?"READ RECORDING":c->capture_stage<=2?"PREPARE CARD":
            c->capture_stage<=4?"SAVE ORIGINAL":c->capture_stage==5?"ANALYZE CHOPS":"BUILD WAVE";
        text(b,font,2,message_y,0,"%s%s",phase,dots);
    }
    else if(c->busy&&c->operation==LOAD)text(b,font,2,message_y,0,"LOAD +DRIVE NO:CANCEL");
    else if(c->result==-15)text(b,font,2,message_y,0,"RECORD: STOP AT TRIM");
    else if(c->operation==LOAD&&c->result)text(b,font,2,message_y,0,"LOAD ERR %d D:SOURCE",c->result);
    else if(c->busy||c->result)text(b,font,2,message_y,0,"%s",c->busy?"WORKING NO:CANCEL":c->result==SL_INSUFFICIENT?"COUNT: NEED MORE HITS":"ERROR NO:BACK");
    else if(!c->wave.source)text(b,font,2,message_y,0,"NO:BACK");
    else if(c->pending_analysis&&!c->loop&&c->tools)text(b,font,2,message_y,0,"CHOP / ANALYSIS PENDING");
    else if(c->status==READY){
        if(c->tools)text(b,font,2,message_y,0,"SETTINGS / %s",c->loop?"LOOP":"CHOP");
        else{
            if(c->loop)text(b,font,2,message_y,0,"LOOP %02u %ub Y:DO",c->session.count?c->session.selected+1:0,c->beats);
            else text(b,font,2,message_y,0,"%s %02u/%02u%s Y:DO",mode,c->session.count?c->session.selected+1:0,c->session.count,c->chop_method==COUNT&&c->count_index==1&&!c->found_count?" FULL":c->chop_method==COUNT&&c->found_count<c->session.count?" FILL":"");
            show_action=1;
        }
    }
    else if(c->loop&&c->operation==PREVIEW&&c->output_frames*c->session.channels*4>0x1fffa0)
        text(b,font,2,message_y,0,"ONE CYCLE NO:BACK");
    else{ text(b,font,2,message_y,0,"%s N:BACK",c->status==IN_POOL?"IN POOL":c->status==SAVED?"SAVED":c->rendered_revision==c->revision?"PLAYING":"PREV PLAY");show_action=1;}
    if(show_action&&!c->tools)text(b,font,98,8,0,"%s",short_action);
    if(!c->busy&&!c->result&&c->hint_ticks&&!c->tools){
        box(b,0,8,127,14,0);
        if(c->hint==10)text(b,font,2,8,0,"MARKERS RESTORED");
        else if(c->hint==11)text(b,font,2,8,0,"NO MARKER UNDO");
        else if(c->hint==2)text(b,font,2,8,0,"FADE %ums: EDGE BLEND",c->fade_ms);
        else if(c->hint==1){
            if(c->loop)text(b,font,2,8,0,"FINE START %ufr",r.start);
            else if(c->chop_method==AUTO)text(b,font,2,8,0,"SENS %u: CHOP DENSITY",c->sensitivity);
            else text(b,font,2,8,0,c->chop_method==COUNT?"COUNT %u: REAL HITS":"EVEN %u: EQUAL PARTS",c->count_index);
        }
        else if(c->hint==3)text(b,font,2,8,0,"MODE: TURN D IN SETTINGS");
        else if(c->hint==5&&c->loop)text(b,font,2,8,0,"BPM %u",c->bpm);
        else if(c->hint==4||c->hint==5)text(b,font,2,8,0,"%s %ufr",c->hint==4?"START":"END",c->hint==4?r.start:r.end);
        else if(c->hint==6)text(b,font,2,8,0,"LOOP %u BEATS",c->beats);
        else if(c->hint==7)text(b,font,2,8,0,c->pan?"PAN - G PUSH:ZOOM":"VIEW %ufr",c->view_span);
    }
    if(can_draw&&!range_ok&&!c->busy&&!c->result){box(b,0,message_y,127,message_y+6,0);text(b,font,2,message_y,0,"%s",c->session.count?"RANGE - CHECK LOOP":"SILENCE: NO CHOPS");}
    if(can_draw&&range_ok&&!c->busy&&!c->result){SLRegion f;if(fade_region(c,r,&f)<0){box(b,0,message_y,127,message_y+6,0);text(b,font,2,message_y,0,"FADE NEEDS HANDLES");}}
    if(c->result){
        int error_y=c->tools?32:16;
        box(b,0,error_y,127,error_y+6,0);
        if(c->operation==CAPTURE)text(b,font,2,error_y,0,"%s %d",
            c->capture_stage==1?"READ RECORDING":c->capture_stage==2?"ORIGINAL FOLDER":
            c->capture_stage==3?"ORIGINAL WRITE":c->capture_stage==4?"ORIGINAL SAVE":c->capture_stage==5?"CHOP ANALYSIS":"BUILD WAVE",c->result);
        else if(c->operation==SAVE_ALL_POOL)text(b,font,2,error_y,0,"ALL POOL ERROR %d",c->result);
        else if(c->operation==REANALYZE&&c->result==SL_INSUFFICIENT)text(b,font,2,error_y,0,"FOUND %u NEED %u",c->found_count,c->count_index);
        else text(b,font,2,error_y,0,"%s",c->operation==REANALYZE&&c->result==SL_LIMIT?"TOO MANY ATTACKS":c->result==-3?"NEED FADE HANDLES":c->result==-13?"PREVIEW TOO LONG":c->result==-12?"NOT ENOUGH MEMORY":c->result==-10?"CANCELLED":c->result==-6?"RANGE TOO LONG":"CHECK RANGE / CARD");
    }
}
U sl_lab_debug(void){return (U)(sl_uptr)active;}
U sl_lab_contexts(void){return contexts;}
void sl_lab_visual(U *out){
    Lab *c=active;SLRegion r={0,0},f={0,0};U i;int ok=0,fa=0;
    for(i=0;i<16;i++)out[i]=0;
    if(!c)return;
    if(!c->busy&&valid(c)&&c->wave.source){ok=shown_region(c,&r);fa=fade_region(c,r,&f);}
    out[0]=c->view_start;out[1]=c->view_span;out[2]=c->zoom;out[3]=r.start;out[4]=r.end;
    out[5]=f.start;out[6]=f.end;out[7]=(U)ok;out[8]=c->cache_span;out[9]=c->wave.peak;
    out[10]=sl_wave_bytes(c->session.frames);out[11]=c->tools;out[12]=c->pan;
    out[13]=(U)(sl_uptr)c->detail;out[14]=(U)(sl_uptr)c->overview;out[15]=(U)fa;
}
void sl_lab_observe(U *out){
    Lab *c=active;U i;
    for(i=0;i<16;i++)out[i]=0;
    if(!c)return;
    out[0]=c->status;out[1]=c->busy;out[2]=c->refs;out[3]=(U)(sl_uptr)c->source;
    out[4]=c->session.frames;out[5]=c->session.channels;out[6]=c->session.count;
    out[7]=c->session.selected;out[8]=c->output_frames;out[9]=c->staged;
    out[10]=c->original_id;out[11]=c->kept;out[12]=c->revision;
    out[13]=(U)c->result;out[14]=(U)(sl_uptr)c->model;out[15]=c->cancel;
}
