/* OS 1.17 Repair4 System Credits binding; not installed in firmware.
 * All PCM must be an owned immutable snapshot. No borrowed RPC pointers.
 * Native FS ABI mapped from the selected image and exercised by the fixture.
 */
#include "engine.h"
#define FN(a,t) ((t)(sl_uptr)(a))
typedef int (*I0)(void);
typedef int (*IP)(void *);
typedef int (*II)(sl_u32);
typedef int (*PATH)(const char *,sl_u32 *,sl_u32 *,sl_u32,sl_u32);
typedef int (*ADD)(sl_u32,const char *,sl_u32);
typedef int (*WRITE)(const void *,sl_u32,void *);
typedef int (*READ)(void *,sl_u32,void *);
typedef int (*OPEN)(const char *,const char *,void *);
typedef void (*LOCK)(void *);
typedef struct { sl_u32 id,position,write,open; } NativeFile;
typedef sl_u32 *(*RECORD)(sl_u32);
enum { SL_EXISTS=-7, SL_IO=-8, SL_NATIVE_RANGE=-9,
       SL_CANCELLED=-10, SL_CLEANUP=-11 };

static int output_path(const char *p) {
    static const char prefix[]="/recorded/";
    sl_u32 i;
    if(!p)return 0;
    for(i=0;i<sizeof(prefix)-1;i++)if(p[i]!=prefix[i])return 0;
    for(;i<192;i++) {
        unsigned char c=(unsigned char)p[i];
        if(!c)return i>sizeof(prefix)-1;
        if(!((c>='A'&&c<='Z')||(c>='a'&&c<='z')||
             (c>='0'&&c<='9')||c=='-'||c=='_'||c==' '))return 0;
    }
    return 0;
}

/* An unlinked staging record is invisible to the sample browser until commit.
 * Native preview opens by reference, so it can audition this record directly. */
static int stage_new(NativeFile *f) {
    void *mutex=(void *)(sl_uptr)0x46f4deb0;
    sl_u32 id;
    f->open=0;
    if(!*(volatile sl_u32 *)(sl_uptr)0x44f2bd68)return SL_IO;
    FN(0x400015a0,LOCK)(mutex);
    id=(sl_u32)FN(0x4015ba88,I0)();
    if(id) {
        f->id=id;f->position=0;f->write=1;f->open=1;
        FN(0x4015bf40,II)(id);
    }
    FN(0x400016d2,LOCK)(mutex);
    return id?SL_OK:SL_IO;
}
int sl_native_discard(sl_u32 id) {
    typedef void (*TRUNCATE)(void *,sl_u32);
    sl_u32 *record;int rc=SL_CLEANUP;
    void *mutex=(void *)(sl_uptr)0x46f4deb0;
    if(!id)return SL_INVALID;
    FN(0x400015a0,LOCK)(mutex);
    if(FN(0x4015bc1a,II)(id)&&!FN(0x4015bf92,II)(id)) {
        record=FN(0x4015b92a,RECORD)(id);
        /* Only an unlinked ordinary file owned by the caller can be freed. */
        if(record&&record[2]==0&&((unsigned char *)record)[0]==0) {
            FN(0x4015614e,TRUNCATE)(record,id);
            FN(0x4015b868,IP)(record);FN(0x4015b846,IP)(record);
            FN(0x4015bb7e,II)(id);rc=SL_OK;
        } else if(record)FN(0x4015b846,IP)(record);
    }
    FN(0x400016d2,LOCK)(mutex);
    return rc;
}
int sl_native_commit(const char *path,sl_u32 id,volatile sl_u32 *cancel) {
    sl_u32 parent=0,offset=0,*record;int flags,rc=SL_IO;
    void *mutex=(void *)(sl_uptr)0x46f4deb0;
    if(!output_path(path)||!id)return SL_INVALID;
    FN(0x400015a0,LOCK)(mutex);
    flags=FN(0x4015a22c,PATH)(path,&parent,&offset,0,0);
    if(cancel&&*cancel)rc=SL_CANCELLED;
    else if((flags&0x0a)==0x0a)rc=SL_EXISTS;
    else if((flags&0x19)==0x19&&FN(0x4015bc1a,II)(id)) {
        record=FN(0x4015b92a,RECORD)(id);
        if(record&&record[2]==0&&((unsigned char *)record)[0]==0) {
            FN(0x4015b846,IP)(record);
            rc=FN(0x40156bd8,ADD)(parent,path+offset,id)==0?SL_OK:SL_IO;
        } else if(record)FN(0x4015b846,IP)(record);
    }
    FN(0x400016d2,LOCK)(mutex);
    return rc;
}
static void be32(unsigned char *p,sl_u32 n) {
    p[0]=(unsigned char)(n>>24);p[1]=(unsigned char)(n>>16);
    p[2]=(unsigned char)(n>>8);p[3]=(unsigned char)n;
}
static sl_u32 get32(const unsigned char *p) {
    return ((sl_u32)p[0]<<24)|((sl_u32)p[1]<<16)|((sl_u32)p[2]<<8)|p[3];
}
/* Only roll back the record this operation actually created. Do not nest
 * the public remover (which locks the same nonrecursive mutex). */
int sl_native_rollback(const char *path,sl_u32 owned_id) {
    typedef int (*REMOVE)(const char *,int);
    void *mutex=(void *)(sl_uptr)0x46f4deb0;
    sl_u32 id=0,offset=0;int flags,rc=SL_CLEANUP;
    if(!output_path(path)||!owned_id)return SL_INVALID;
    FN(0x400015a0,LOCK)(mutex);
    flags=FN(0x4015a22c,PATH)(path,&id,&offset,0,0);
    if((flags&0x19)==0x19)rc=SL_OK; /* already absent */
    else if((flags&0x0a)==0x0a&&id==owned_id)
        rc=FN(0x40156e94,REMOVE)(path,0)==0?SL_OK:SL_CLEANUP;
    FN(0x400016d2,LOCK)(mutex);
    return rc;
}
sl_u32 sl_native_lookup(const char *path) {
    sl_u32 id=0,offset=0;int flags;
    void *mutex=(void *)(sl_uptr)0x46f4deb0;
    FN(0x400015a0,LOCK)(mutex);
    flags=FN(0x4015a22c,PATH)(path,&id,&offset,0,0);
    FN(0x400016d2,LOCK)(mutex);
    return (flags&0x0a)==0x0a?id:0;
}
int sl_native_reference(sl_u32 id,sl_u32 *reference) {
    typedef int (*REF)(sl_u32,sl_u32 *);
    return FN(0x4015aec2,REF)(id,reference);
}
int sl_native_preview(sl_u32 id) {
    typedef unsigned char (*PREVIEW)(void *,sl_u32 *);
    sl_u32 reference[4];int rc=sl_native_reference(id,reference);
    if(rc)return SL_IO;
    return FN(0x40030b10,PREVIEW)(0,reference)?SL_OK:SL_IO;
}
int sl_native_assign_pool(sl_u32 id) {
    typedef void *(*GET)(void);
    typedef unsigned char (*ASSIGN)(void *,sl_u32 *,int);
    sl_u32 reference[4],*record,linked,tries,i,j,slot=1024;
    unsigned char *data,*ref=(unsigned char *)reference;
    void *project,*settings;
    void *mutex=(void *)(sl_uptr)0x46f4deb0;
    FN(0x400015a0,LOCK)(mutex);
    record=FN(0x4015b92a,RECORD)(id);linked=record?record[2]:0;
    if(record)FN(0x4015b846,IP)(record);
    FN(0x400016d2,LOCK)(mutex);
    if(!linked)return SL_INVALID;
    if(sl_native_reference(id,reference))return SL_IO;
    project=FN(0x40198b7e,GET)();
    if(!project)return SL_IO;
    /* Every stock loadSample caller passes the Project settings subobject,
     * not the root Project returned by the getter. The root has a separate
     * pool-shaped data area, so writing it does not populate the stock UI. */
    settings=FN(0x40041d18,void *(*)(void *))(project);
    if(!settings)return SL_IO;
    /* -1 repeatedly targets the same UI-selected slot in a single batch.
     * Choose a genuinely empty native pool entry; the first 17 slots are
     * stock reserved, and every 0x80th slot is excluded by the loader. */
    {
        sl_u32 *vt=(sl_u32 *)*(sl_u32 *)settings;
        data=((void *(*)(void *))(sl_uptr)vt[10])(settings);
        if(!data)return SL_IO;
        data+=0x1d2;
        for(i=17;i<1024;i++)if((i&0x7f)&&data[i*16]==0xff&&data[i*16+1]==0xff&&
                              data[i*16+2]==0xff&&data[i*16+3]==0xff){slot=i;break;}
    }
    if(slot==1024||!FN(0x4004b502,ASSIGN)(settings,reference,(int)slot))return SL_IO;
    /* Wait until the native project contains the requested reference before
     * staging the next batch member. */
    for(tries=0;tries<200;tries++){
        sl_u32 *vt=(sl_u32 *)*(sl_u32 *)settings;
        data=((void *(*)(void *))(sl_uptr)vt[10])(settings);
        if(data){
            data+=0x1d2;
            for(j=0;j<16&&data[slot*16+j]==ref[j];j++);
            if(j==16)return SL_OK;
        }
        FN(0x40136544,void (*)(sl_u32))(10);
    }
    return SL_IO;
}

/* Alternative to retaining a recorder reply: read a saved native original
 * through its own native file handle into a fresh caller-owned PCM snapshot.
 * On any I/O failure the session remains invalid; discard the destination.
 * This does not yet provide the UI handoff after stock recording Save.
 */
int sl_native_import(const char *path,sl_i16 *pcm,sl_u32 capacity,
                     SLSession *session,sl_u32 generation) {
    NativeFile f;unsigned char header[64],tail[16];sl_u32 i,bytes,channels,frames,n;
    unsigned char *cursor=(unsigned char *)pcm;int rc=SL_IO;
    if(!path||!pcm||!session||!capacity||capacity>SL_MAX_FRAMES)return SL_INVALID;
    /* Refuse overwrite of an existing immutable session's source. */
    if(session->source) {
        sl_uptr lo=(sl_uptr)pcm,old=(sl_uptr)session->source;
        sl_uptr hi=lo+capacity*4,oldhi=old+session->frames*session->channels*2;
        if(hi<lo||oldhi<old||(lo<oldhi&&old<hi))return SL_OVERLAP;
    }
    for(i=0;i<192&&path[i];i++);
    if(i==192)return SL_INVALID;
    sl_invalidate(session);
    f.open=0;
    if(FN(0x40158902,OPEN)(path,"r",&f))return SL_IO;
    if(FN(0x4015860a,READ)(header,64,&f)!=64)goto done;
    channels=header[1]==1?2:1;bytes=get32(header+4);
    if(header[1]>1||get32(header+8)!=48000||!bytes||bytes%(channels*2)) {
        rc=SL_INVALID;goto done;
    }
    frames=bytes/(channels*2);
    if(frames>capacity||frames>SL_MAX_FRAMES){rc=SL_CAPACITY;goto done;}
    while(bytes) {
        n=bytes>0x8000?0x8000:bytes;
        if(FN(0x4015860a,READ)(cursor,n,&f)!=(int)n)goto done;
        cursor+=n;bytes-=n;
    }
    if(FN(0x4015860a,READ)(tail,16,&f)!=16)goto done;
    rc=SL_OK;
done:
    if(FN(0x401588c0,IP)(&f))rc=SL_IO;
    if(!rc)rc=sl_open(session,pcm,frames,channels,48000,generation);
    return rc;
}

/* Direct +Drive reference snapshot. Reuses SMART's hardware-accepted native
 * read-only handle/identity/read contracts. Native sample content is big-
 * endian16-bit PCM at48k, not a RIFF stream. No preview/track/recorder route.
 * Every allocation and native open is released on failure/cancellation. */
int sl_native_read_ref(const sl_u32 *ref,sl_i16 **pcm,sl_u32 *frames,
                       sl_u32 *channels,volatile sl_u32 *cancel) {
    NativeFile f;unsigned char header[64],verify[64],tail[16];
    sl_u32 id=0,canonical[4],bytes,ch,at,take,*record,i;sl_i16 *owned=0;
    void *mutex=(void *)(sl_uptr)0x46f4deb0;int rc=SL_IO,locked=0;
    if(!ref||!pcm||!frames||!channels)return SL_INVALID;
    *pcm=0;*frames=*channels=0;
    if(cancel&&*cancel)return SL_CANCELLED;
    if(FN(0x4015b454,int (*)(const sl_u32 *,sl_u32 *))(ref,&id)<0||!id)return SL_STALE;
    if(sl_native_reference(id,canonical))return SL_STALE;
    FN(0x400015a0,LOCK)(mutex);
    if(!FN(0x4015bc1a,II)(id)){FN(0x400016d2,LOCK)(mutex);return SL_IO;}
    f.id=id;f.position=0;f.write=0;f.open=1;FN(0x4015bf40,II)(id);
    FN(0x400016d2,LOCK)(mutex);
    if(FN(0x4015860a,READ)(header,64,&f)!=64)goto done;
    bytes=get32(header+4);ch=header[1]==1?2:1;
    if(header[1]>1||get32(header+8)!=48000||!bytes||bytes%(ch*2)){rc=SL_INVALID;goto done;}
    if(bytes/(ch*2)>SL_MAX_FRAMES){rc=SL_LIMIT;goto done;}
    owned=FN(0x4011ebd6,void *(*)(sl_u32))(bytes);if(!owned){rc=-12;goto done;}
    FN(0x400015a0,LOCK)(mutex);locked=1;
    record=FN(0x4015b92a,RECORD)(id);
    if(!record){rc=SL_STALE;goto done;}
    rc=(record[3]==canonical[1]&&record[1]==canonical[2]&&record[4]==canonical[3])?0:SL_STALE;
    FN(0x4015b846,IP)(record);if(rc)goto done;
    rc=SL_IO;
    if(FN(0x4015824e,int (*)(sl_u32,sl_u32,sl_u32,void *))(id,0,64,verify)!=64)goto done;
    for(i=0;i<64;i++)if(header[i]!=verify[i]){rc=SL_STALE;goto done;}
    for(at=0;at<bytes;at+=take){
        if(cancel&&*cancel){rc=SL_CANCELLED;goto done;}
        take=bytes-at;if(take>0x8000)take=0x8000;
        if(FN(0x4015824e,int (*)(sl_u32,sl_u32,sl_u32,void *))(id,64+at,take,(unsigned char *)owned+at)!=(int)take)goto done;
    }
    if(FN(0x4015824e,int (*)(sl_u32,sl_u32,sl_u32,void *))(id,64+bytes,16,tail)!=16)goto done;
    rc=cancel&&*cancel?SL_CANCELLED:SL_OK;
done:
    if(locked)FN(0x400016d2,LOCK)(mutex);
    if(FN(0x401588c0,IP)(&f))rc=SL_IO;
    if(rc){if(owned)FN(0x4011ebdc,void (*)(void *))(owned);}
    else{*pcm=owned;*frames=bytes/(ch*2);*channels=ch;}
    return rc;
}

/* Create-only export, cancellable between bounded writes. Failure closes
 * and rolls back this exact newly-created record before returning. This
 * handles operation failures, not arbitrary power loss during native FS I/O.
 */
int sl_native_stage_repeat(const sl_i16 *pcm,sl_u32 frames,
                     sl_u32 channels,sl_u32 repeats,sl_u32 *record_id,volatile sl_u32 *cancel) {
    NativeFile f;unsigned char header[64],tail[16];sl_u32 i,n,bytes,pass;
    const unsigned char *cursor=(const unsigned char *)pcm;int rc;
    if(!pcm||!record_id||!frames||frames>SL_MAX_FRAMES||
       (channels!=1&&channels!=2)||!repeats||repeats>2||frames>SL_MAX_FRAMES/repeats)return SL_INVALID;
    if(cancel&&*cancel)return SL_CANCELLED;
    bytes=frames*channels*2;
    for(i=0;i<64;i++)header[i]=0;
    for(i=0;i<16;i++)tail[i]=0;
    header[1]=(unsigned char)(channels==2);header[0x14]=0x7f;
    be32(header+4,bytes*repeats);be32(header+8,48000);
    rc=stage_new(&f);if(rc)return rc;
    if(FN(0x4015866a,WRITE)(header,64,&f)!=64)rc=SL_IO;
    for(pass=0;!rc&&pass<repeats;pass++) {
    bytes=frames*channels*2;cursor=(const unsigned char *)pcm;
    while(!rc&&bytes) {
        if(cancel&&*cancel){rc=SL_CANCELLED;break;}
        n=bytes>0x8000?0x8000:bytes;
        if(FN(0x4015866a,WRITE)(cursor,n,&f)!=(int)n)rc=SL_IO;
        cursor+=n;bytes-=n;
    }
    }
    if(!rc&&FN(0x4015866a,WRITE)(tail,16,&f)!=16)rc=SL_IO;
    if(!rc&&FN(0x4015b3aa,IP)(&f)==-1)rc=SL_IO;
    if(!rc&&cancel&&*cancel)rc=SL_CANCELLED;
    if(FN(0x401588c0,IP)(&f))rc=SL_IO;
    if(rc&&sl_native_discard(f.id))return SL_CLEANUP;
    if(!rc)*record_id=f.id;
    return rc;
}
int sl_native_stage(const sl_i16 *pcm,sl_u32 frames,sl_u32 channels,
                    sl_u32 *record_id,volatile sl_u32 *cancel) {
    return sl_native_stage_repeat(pcm,frames,channels,1,record_id,cancel);
}
int sl_native_export_control(const char *path,const sl_i16 *pcm,sl_u32 frames,
                     sl_u32 channels,sl_u32 *record_id,volatile sl_u32 *cancel) {
    sl_u32 id=0;int rc;
    if(!output_path(path)||!record_id)return SL_INVALID;
    if(sl_native_lookup(path))return SL_EXISTS;
    rc=sl_native_stage(pcm,frames,channels,&id,cancel);if(rc)return rc;
    rc=sl_native_commit(path,id,cancel);
    if(rc) {if(sl_native_discard(id))return SL_CLEANUP;return rc;}
    *record_id=id;return SL_OK;
}
int sl_native_export(const char *path,const sl_i16 *pcm,sl_u32 frames,
                     sl_u32 channels,sl_u32 *record_id) {
    return sl_native_export_control(path,pcm,frames,channels,record_id,0);
}

/* Apply an analyzed selection through the real native trim setters. Current
 * recorder trim state, exact frame count and 144-frame native minimum are
 * required. No new event IDs or invented view fields. Generation is the
 * caller-owned session epoch, NOT the view waveform-refresh counter.
 * Admission precondition for a future UI owner: prove that this view still
 * represents the SAME recording as the snapshot. Equal lengths alone are
 * not source identity; the supplied-PCM fixture provides that linkage here.
 */
int sl_native_region(void *view,SLSession *session,sl_u32 generation,sl_u32 index) {
    typedef void *(*GET)(void);
    typedef int (*STATE)(void *);
    typedef void (*DELTA)(void *,int);
    volatile sl_u32 *start,*end;
    SLRegion r;sl_u32 total;int rc;
    if(!view||!session)return SL_INVALID;
    if(generation!=session->generation||!session->source)return SL_STALE;
    if(index>=session->count)return SL_INVALID;
    if(FN(0x40015f6c,STATE)(FN(0x40199b66,GET)())!=4)return SL_STALE;
    total=(sl_u32)FN(0x400cd832,I0)();
    r=session->regions[index];
    if(total!=session->frames||r.end>total||r.start>=r.end||r.end-r.start<144)
        return SL_NATIVE_RANGE;
    rc=sl_select(session,generation,index);if(rc)return rc;
    start=(volatile sl_u32 *)((unsigned char *)view+0x9a0);
    end=(volatile sl_u32 *)((unsigned char *)view+0x9a4);
    /* Expand end first, then start, then end: prevents the old selected
     * region's minimum-length clamp from corrupting a distant selection. */
    FN(0x400202ca,DELTA)(view,(int)(total-*end));
    FN(0x4002028c,DELTA)(view,(int)(r.start-*start));
    FN(0x400202ca,DELTA)(view,(int)(r.end-*end));
    return *start==r.start&&*end==r.end?SL_OK:SL_NATIVE_RANGE;
}

/* One tested editor extension: A selects a chop in native trim state, where
 * stock uses D-H. Return 0 to delegate every other event to the original
 * handler. No persistent parameter allocation. Not yet installed as a hook.
 */
int sl_native_encoder(void *view,SLSession *session,sl_u32 generation,void *event) {
    typedef int (*IS)(void *,int);
    typedef int (*DELTA)(void *,int,int);
    typedef void *(*GET)(void);
    typedef int (*STATE)(void *);
    int step,index,rc;
    if(!view||!session||!event)return 0;
    if(FN(0x40015f6c,STATE)(FN(0x40199b66,GET)())!=4)return 0;
    if(!FN(0x40111a12,IS)(event,0))return 0;
    if(!session->source||!session->count||generation!=session->generation)return SL_STALE;
    step=FN(0x40111aaa,DELTA)(event,1,1);
    if(step>64)step=64;
    if(step< -64)step= -64;
    index=(int)session->selected+step;
    if(index<0)index=0;
    if((sl_u32)index>=session->count)index=(int)session->count-1;
    rc=sl_native_region(view,session,generation,(sl_u32)index);
    return rc?rc:1;
}
