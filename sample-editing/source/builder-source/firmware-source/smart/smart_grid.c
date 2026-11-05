/* Original native grid adapter, AGPL-3.0-or-later. No recording-service peer
 * dependency, file writes, sample replacement or new serialization format. */
#include "../sample_lab/engine.h"
typedef sl_u32 U;
#define N(a,t) ((t)(sl_uptr)(a))
typedef struct {U id,position,write,open;} File;
typedef struct Node Node;
struct Node {Node *next,*prev;U start,end,flags;};
typedef struct {U data[2],manager,invoke;} Fn;
typedef struct {void *object,*control;} SharedText;
const char smart_name[]="SMART";
const U smart_quick_counts[]={1,2,4,8,16,32,64};
typedef void *(*GET)(void);
typedef void *(*DATA)(void *);
extern void smart_make_text(SharedText *,const char *);
static volatile int smart_last_status;
static char smart_feedback[24]="SMART READY";
static char smart_failure[24]="SMART -00 GRID KEPT";
/* S=Sound slot, E/S/N=empty/sentinel/nonempty project reference,
 * Q=exact signed native resolver result, I=full 32-bit returned file ID. */
static char smart_ref_note[24]="S0000N Q-1 I00000000";
static U smart_ref_slot,smart_ref_class,smart_resolve_id;
static int smart_resolve_result;
static U get32(unsigned char *p){return ((U)p[0]<<24)|((U)p[1]<<16)|((U)p[2]<<8)|p[3];}
static void *get(U a){return N(a,GET)();}
static void *data(void *o){return o?((DATA)(sl_uptr)(*(U **)o)[10])(o):0;}
static void *sound(void *project,U track){
    return N(0x40035cca,void *(*)(void *,U))(N(0x40041d24,void *(*)(void *))(project),track);
}
static int same(const U *a,const U *b){return a[0]==b[0]&&a[1]==b[1]&&a[2]==b[2]&&a[3]==b[3];}
int smart_status(void){return smart_last_status;}

/* This is the stock slice-grid error ownership sequence: construct a shared
 * text object, transfer it to the controller notification call, then release
 * the local owner. SMART retains no UI state after it returns. */
static void smart_notice(void *controller,const char *text){
    SharedText owned={0,0},message;
    if(!controller)return;
    smart_make_text(&owned,text);
    message=owned;owned.object=0;owned.control=0;
    N(0x4011b22a,int (*)(void *,SharedText *,U))(controller,&message,0);
    /* Native release expects the control-word address, as stock4005fec8.
     * Passing &message corrupts the notification View's interface vtable. */
    N(0x40197d26,void (*)(void **))(&message.control);
}
static void smart_ref_text(void){
    static const U place[4]={1000,100,10,1};
    U n=smart_ref_slot,i,j,digit,v=smart_resolve_id;
    for(i=0;i<4;i++){
        digit=0;while(n>=place[i]){n-=place[i];digit++;}
        smart_ref_note[1+i]='0'+digit;
    }
    smart_ref_note[5]=(char)smart_ref_class;
    smart_ref_note[8]=smart_resolve_result<0?'-':'+';
    smart_ref_note[9]='0'+(smart_resolve_result<0?-smart_resolve_result:smart_resolve_result);
    for(i=0;i<8;i++){
        j=(v>>(28-4*i))&15;
        smart_ref_note[12+i]=(char)(j<10?'0'+j:'A'+j-10);
    }
}
static int smart_fail(void *controller,int status){
    U error=status<0?(U)(-status):99,tens=0;
    if(error>99)error=99;
    while(error>=10){error-=10;tens++;}
    smart_failure[7]='0'+tens;
    smart_failure[8]='0'+error;
    smart_last_status=status;
    if(status==-51){smart_ref_text();smart_notice(controller,smart_ref_note);}
    else smart_notice(controller,smart_failure);
    return status;
}
static void smart_success_text(U found){
    U tens=0,ones=found;
    smart_feedback[0]='S';smart_feedback[1]='M';smart_feedback[2]='A';
    smart_feedback[3]='R';smart_feedback[4]='T';smart_feedback[5]=' ';
    /* Keep this self-contained ColdFire code free of compiler divide helpers.
     * SMART's bounded result is 2..64, so repeated subtraction is trivial. */
    while(ones>=10){ones-=10;tens++;}
    if(found>=10){smart_feedback[6]='0'+tens;smart_feedback[7]='0'+ones;
        smart_feedback[8]=' ';smart_feedback[9]='S';smart_feedback[10]='L';
        smart_feedback[11]='I';smart_feedback[12]='C';smart_feedback[13]='E';
        smart_feedback[14]='S';smart_feedback[15]=0;
    }else{smart_feedback[6]='0'+found;smart_feedback[7]=' ';smart_feedback[8]='S';
        smart_feedback[9]='L';smart_feedback[10]='I';smart_feedback[11]='C';
        smart_feedback[12]='E';smart_feedback[13]='S';smart_feedback[14]=0;}
}

/* Exported separately for native file/reference tests; no invented pathname. */
int smart_read(const U *ref,sl_i16 **pcm,U *frames,U *channels){
    File f;unsigned char header[64],verify[64],tail[16];U id=0,n,bytes,at,*record,i,canonical[4];int rc=-8,locked=0,resolved;
    void *mutex=(void *)(sl_uptr)0x46f4deb0;sl_i16 *owned=0;
    if(!ref||!pcm||!frames||!channels)return -1;
    *pcm=0;*frames=0;*channels=0;
    /* Stock callers accept every nonnegative resolved-reference result.
     * A strict ==1 rejects other valid native reference variants. */
    resolved=N(0x4015b454,int (*)(const U *,U *))(ref,&id);
    smart_resolve_result=resolved;smart_resolve_id=id;
    if(resolved<0||!id)return -51;
    /* Resolve the current native record for this ID before checking it.
     * Stock accepts reference aliases; the project reference is checked
     * again before grid commit, while this snapshot must match the file. */
    if(N(0x4015aec2,int (*)(U,U *))(id,canonical))return -52;
    /* Same open-reference acquisition as native fopen's existing-file branch.
     * Native close releases it. A read handle never creates/truncates a file. */
    N(0x400015a0,void (*)(void *))(mutex);
    if(!N(0x4015bc1a,int (*)(U))(id)){N(0x400016d2,void (*)(void *))(mutex);return -55;}
    f.id=id;f.position=0;f.write=0;f.open=1;N(0x4015bf40,void (*)(U))(id);
    N(0x400016d2,void (*)(void *))(mutex);
    if(N(0x4015860a,int (*)(void *,U,File *))(header,64,&f)!=64)goto done;
    bytes=get32(header+4);n=header[1]==1?2:1;
    if(header[1]>1||get32(header+8)!=48000||!bytes||bytes%(n*2)){rc=-1;goto done;}
    *frames=bytes/(n*2);*channels=n;
    if(*frames>SL_MAX_FRAMES){rc=-6;goto done;}
    owned=N(0x4011ebd6,void *(*)(U))(bytes);if(!owned){rc=-12;goto done;}
    /* fread's internal read runs under this SAME mutex in stock. Hold it
     * across the snapshot, using that internal read rather than recursively
     * taking the mutex through fread. No writer can interleave PCM chunks. */
    N(0x400015a0,void (*)(void *))(mutex);locked=1;
    record=N(0x4015b92a,U *(*)(U))(id);
    if(!record){rc=-52;goto done;}
    rc=(record[3]==canonical[1]&&record[1]==canonical[2]&&record[4]==canonical[3])?0:-52;
    N(0x4015b846,void (*)(U *))(record);if(rc)goto done;
    rc=-8;
    if(N(0x4015824e,int (*)(U,U,U,void *))(id,0,64,verify)!=64)goto done;
    for(i=0;i<64;i++)if(header[i]!=verify[i]){rc=-52;goto done;}
    for(at=0;at<bytes;at+=n){
        n=bytes-at;if(n>0x8000)n=0x8000;
        if(N(0x4015824e,int (*)(U,U,U,void *))(id,64+at,n,(unsigned char *)owned+at)!=(int)n)goto done;
    }
    if(N(0x4015824e,int (*)(U,U,U,void *))(id,64+bytes,16,tail)!=16)goto done;
    rc=0;
done:
    if(locked)N(0x400016d2,void (*)(void *))(mutex);
    if(N(0x401588c0,int (*)(File *))(&f))rc=-8;
    if(rc){if(owned)N(0x4011ebdc,void (*)(void *))(owned);*frames=0;*channels=0;}
    else *pcm=owned;
    return rc;
}

/* Native callback capture is {view/controller, track}. Fixed grids keep their
 * entire original callback, including ordinary TR.DET and confirmation. */
int smart_grid(Fn *fn,int count,int transient){
    U *capture=(U *)(sl_uptr)fn->data[0],track=capture[1],id,tag,ref[4],project_ref[4],sound_ref[4],now[4],frames=0,ch=0,found=0,i,from_sound=0;
    void *controller=(void *)(sl_uptr)capture[0],*project,*settings,*model,*original_model;unsigned char *d,*pd;sl_i16 *pcm=0;SLRegion regions[64];
    Node root,nodes[64];int rc;
    if(count!=1){
        /* Stock fixed grids divide the actual resident frame span. Fewer
         * frames than rows would create duplicate/zero-width boundaries.
         * Keep stock CLEAR and ordinary fixed-grid behavior; reject only
         * that impossible nonempty request before the native grid writer. */
        if(count>1&&count<=64&&track<16){
            void *p=get(0x40198b7e),*m=p?sound(p,track):0;
            if(m){
                int slot=(short)N(0x40050e4e,U (*)(void *))(m);
                if(slot>0&&slot<1024&&(slot&127)){
                    U n=N(0x40153a94,U (*)(U))((U)slot);
                    U shift=N(0x40153aac,U (*)(U))((U)slot)&255;
                    if(n&&shift<=1&&(n>>shift)<(U)count)return smart_fail(controller,-1);
                }
            }
        }
        return N(0x4005fe3e,int (*)(Fn *,int,int))(fn,count,transient);
    }
    if(track>=16)return smart_fail(controller,-1);
    project=get(0x40198b7e);if(!project)return smart_fail(controller,-54);
    settings=N(0x40041d18,void *(*)(void *))(project);if(!settings)return smart_fail(controller,-54);
    model=sound(project,track);original_model=model;d=data(model);pd=data(settings);
    if(!d||!pd)return smart_fail(controller,-54);
    id=(U)*(unsigned short *)(d+0x4c);if(id>=1024||!(id&127))return smart_fail(controller,-1);
    for(i=0;i<4;i++)project_ref[i]=*(U *)(pd+0x1d2+id*16+i*4);
    smart_ref_slot=id;
    smart_ref_class=project_ref[0]==0xffffffffu?'S':
        (project_ref[0]==0&&project_ref[1]==0&&project_ref[2]==0&&project_ref[3]==0?'E':'N');
    for(i=0;i<4;i++)ref[i]=project_ref[i];
    if(project_ref[0]==0xffffffffu){
        /* Stock kit remapping uses Sound+0x440 and matches words 1/2 against
         * the selected project row (0x4008e528 / 0x4004b82c). The project
         * row's sentinel word is not a native file ID. */
        for(i=0;i<4;i++)sound_ref[i]=*(U *)(d+0x440+i*4);
        if(sound_ref[0]==0xffffffffu||sound_ref[1]!=project_ref[1]||
           sound_ref[2]!=project_ref[2])return smart_fail(controller,-56);
        for(i=0;i<4;i++)ref[i]=sound_ref[i];
        from_sound=1;
    }
    tag=N(0x4004aeea,U (*)(void *))(project);
    rc=smart_read(ref,&pcm,&frames,&ch);if(rc)return smart_fail(controller,rc);
    rc=sl_detect(pcm,frames,ch,48000,96,35,regions,64,&found);
    N(0x4011ebdc,void (*)(void *))(pcm);
    if(rc||!found||found>64)return smart_fail(controller,rc?rc:-6);
    if(get(0x40198b7e)!=project||N(0x4004aeea,U (*)(void *))(project)!=tag)return smart_fail(controller,-53);
    if(N(0x40041d18,void *(*)(void *))(project)!=settings)return smart_fail(controller,-53);
    model=sound(project,track);d=data(model);pd=data(settings);
    if(model!=original_model||!d||!pd||(U)*(unsigned short *)(d+0x4c)!=id)return smart_fail(controller,-53);
    for(i=0;i<4;i++)now[i]=*(U *)(pd+0x1d2+id*16+i*4);
    if(!same(now,project_ref))return smart_fail(controller,-53);
    if(from_sound){
        for(i=0;i<4;i++)now[i]=*(U *)(d+0x440+i*4);
        if(!same(now,sound_ref))return smart_fail(controller,-53);
    }
    root.next=nodes;root.prev=nodes+found-1;
    for(i=0;i<found;i++){
        if(regions[i].start>=regions[i].end||regions[i].end>frames)return smart_fail(controller,-1);
        nodes[i].next=i+1<found?nodes+i+1:&root;nodes[i].prev=i?nodes+i-1:&root;
        nodes[i].start=regions[i].start;nodes[i].end=regions[i].end;nodes[i].flags=0;
    }
    N(0x400510f4,void (*)(void *,Node *,U))(model,&root,1);
    smart_last_status=(int)found;
    smart_success_text(found);
    smart_notice(controller,smart_feedback);
    return smart_last_status;
}
