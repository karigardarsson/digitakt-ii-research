/* DT2 OS1.17 native sample viewport, AGPL-3.0-or-later.
 * Reuses native renderers and the native editor's bounded DSPI1 waveform RPC.
 * No PCM ownership, file IO, Sound writes, DSP code or new parameter state.
 */
typedef unsigned int U;
typedef int I;
typedef unsigned char B;
#define N(a,t) ((t)(unsigned long)(a))
#define R(a) (*(volatile U *)(a))
typedef struct {U raw[13];} Widget;
typedef struct {U vt,w,h,stride,pixels,aux,owns;} Bitmap;
extern void stock_tick(void *),stock_active(void *,int);
static struct {
 void *view,*owner,*render_owner;U type,kind,slot,level,pan,frames,ch;
 U pressed,pressed_at,long_done,cache_level,cache_pan,cache_valid,rendering;
 U info[7],factory[15];Widget copy;
 B full[401],record[401],reply[506];
 U record_valid,record_level,record_pan;
} z;
static U render_level,render_pan;
typedef struct {U data[2],manager,invoke;} Fn;
/* One immutable request at a time. Worker never retains or dereferences a
 * SourcePage/widget pointer. Publication state is written last, on this
 * single-core CPU, with compiler barriers; no lock is held across IO. */
static struct {
 volatile U state;
 U view,slot,level,pan,info[7],desc[3],start,end;
 U direct_header,direct_nonzero,direct_peak,header,nonzero,peak,converted,hits;
 I error;B reply[506];
} q;
static U shown_detail_state;
#define BARRIER() __asm__ volatile("" ::: "memory")
static void copy(void *out,const void *in,U n){N(0x40136388,void (*)(void *,const void *,U))(out,in,n);}
static U now(void){return R(0xfc07000c);}
/* Native accumulated DTIM0 ticks are divided by132000000 at400cc970.
 * Button producer4011e342 also timestamps from this same DTIM0 counter. */
static int held_long(void){return z.pressed&&(U)(now()-z.pressed_at)>=105600000u;}
static U span(void){return 65536u>>z.level;}
static int applicable(U type){return type<=4||type==6||type==7;}
static U type(void *v){return N(0x4005f6bc,U (*)(void *))(v);}
static void dirty(void){
 if(z.owner)((B *)z.owner)[z.kind==0?49:45]=1; /* V11 native widget invalidation. */
 if(z.view)N(0x4011b126,void (*)(void *))(z.view);
}
static void reset_level(void){z.level=z.pan=0;z.cache_valid=0;dirty();}
static int context(void *v){return v==z.view&&*(U *)((B *)v+0x90)==1&&applicable(type(v));}
static void long_check(void){if(!z.long_done&&held_long()){z.long_done=1;reset_level();}}
void zoom_tick(void *v){
 stock_tick(v);
 if(v==z.view){
  if(!context(v)){z.pressed=0;return;}long_check();
  if(z.level>=2&&(q.state==3||q.state!=shown_detail_state)){
   shown_detail_state=q.state;dirty();
  }
 }
}
void zoom_active(void *v,int on){if(!on&&v==z.view){z.pressed=z.cache_valid=z.record_valid=0;z.view=z.owner=0;}stock_active(v,on);}
U zoom_pan_encoder(void *);
static U fraction(U,U);
static int metadata(U,U *);
static int info_equal(U *,U *);
static int fit_selected(void){
 U *w=(U *)z.owner,a,b,n,idx,len,wanted,level=0,sz=65536,center,pan,info[7];
 if(!w||w[0]!=z.slot||type(z.view)!=z.type||!metadata(z.slot,info)||!info_equal(info,z.info))return 0;
 if(z.kind==2&&(z.type==6||z.type==7)){
  if(!z.frames||w[1]>=w[2]||w[2]>z.frames)return 0;
  a=fraction(w[1],z.frames);b=fraction(w[2],z.frames);
  if(b<65536)b++; /* conservative exclusive-end projection */
 }else if(z.kind==1&&z.type==4){
  n=w[1];idx=w[2];len=w[3];
  if(!n||n>64||idx>=n)return 0;
  if(len>=n-idx)len=n-idx-1;
  a=idx*65536/n;b=(idx+len+1)*65536/n;
 }else return 0;
 if(b<=a)return 0;
 wanted=b-a;wanted+=wanted/8;center=a+(b-a)/2;
 while(level<6&&sz/2>=wanted){sz/=2;level++;}
 pan=center>sz/2?center-sz/2:0;if(pan>65536-sz)pan=65536-sz;
 if(z.level==level&&z.pan==pan){reset_level();return 1;}
 z.level=level;z.pan=pan;z.cache_valid=0;dirty();return 1;
}
int zoom_key(void *v,void *event){
 U key=*(U *)((B *)event+12),f=*(U *)((B *)event+16),center,sz,encoder;
 if(!context(v))return 0;
 /* A turn remains native. A push only fits the existing selected bounds on
  * waveform Page2 of GRID4/MANUAL6/XSLICE7. Never use projected z.copy. */
 if(key==41&&type(v)==z.type&&(z.type==4||z.type==6||z.type==7)){
  if((f&1)&&!(f&8))fit_selected();
  return 1;
 }
 encoder=zoom_pan_encoder(v);if(!encoder||key!=40+encoder)return 0;
 if(f&16){
  if(!z.pressed)return 0;
  long_check();z.pressed=0;
  if(!z.long_done){
   center=z.pan+span()/2;z.level=(z.level+1)%7;sz=span();
   z.pan=z.level?(center>sz/2?center-sz/2:0):0;
   if(z.pan>65536-sz)z.pan=65536-sz;
   z.cache_valid=0;dirty();
  }
  z.long_done=0;return 1;
 }
 if(f&1){if(!(f&8)){z.pressed=1;z.pressed_at=now();z.long_done=0;}else long_check();return 1;}
 return 0;
}
U zoom_pan_encoder(void *v){
 U *vt=*(U **)v;U (*param)(void *,U)=(void *)vt[47];
 if(type(v)==6)return param(v,6)==0?7:0;
 if(param(v,2)==0)return 3;
 if(param(v,6)==0)return 7;
 return 0;
}
int zoom_encoder(void *v,void *event){
 U knob=*(U *)((B *)event+12),step;I delta=*(I *)((B *)event+16),p;
 if(!context(v)||!z.level||knob!=zoom_pan_encoder(v))return 0;
 step=span()/96;if(!step)step=1;
 if(delta>96)delta=96;
 if(delta< -96)delta=-96;
 p=(I)z.pan+delta*(I)step;if(p<0)p=0;if(p>(I)(65536-span()))p=65536-span();
 if((U)p!=z.pan){z.pan=p;z.cache_valid=0;dirty();}return 1;
}
/* Exact bounded fraction without64-bit division/helpers or large products. */
static U fraction(U a,U n){U i,v=0;if(!n)return 0;if(a>=n)return 65536;
 for(i=0;i<16;i++){a<<=1;v<<=1;if(a>=n){a-=n;v++;}}return v;}
static U frame(U f){return (z.frames>>16)*f+(((z.frames&65535)*f)>>16);}
static I xpos(U f){I n=((I)f-(I)render_pan)*96,s=(I)(65536u>>render_level);return n<0?-((-n+s/2)/s):(n+s/2)/s;}
static I clamp(I x,I a,I b){return x<a?a:x>b?b:x;}
static int metadata(U slot,U *out){U i;for(i=0;i<7;i++)out[i]=0;return slot<1024&&N(0x40153b20,int (*)(U,void *))(slot,out);}
static int info_equal(U *a,U *b){U i;for(i=0;i<7;i++)if(a[i]!=b[i])return 0;return 1;}
static int overview(void){
 U *f=N(0x401a04e4,U *(*)(void))();U p,limit;int ok=0;
 N(0x40001608,void (*)(void *))(f+4);
 p=f[1];limit=f[2];
 if(p&&p<=limit&&limit-p>=401&&z.slot<(limit-p)/401){
  p+=z.slot*401;
  if(R(p)!=R(0x402ab508)){
   copy(z.full,(void *)p,401);ok=1;
  }
 }
 N(0x4000172a,void (*)(void *))(f+4);return ok;
}
/* V11's real 360-column cache: enough detail for 2x, never pretend it is
 * high-resolution data at 4..64x. Keep the native vector lock and identity
 * checks; do not retain its pointer or copy its mutex into our factory. */
static int cached2(void){
 U *f=N(0x401a04e4,U *(*)(void))();U p,limit,i,k,offset;int ok=0;
 N(0x40001608,void (*)(void *))(f+11);
 p=f[8];limit=f[9];
 if(p&&p<=limit&&limit-p>=1457&&z.slot<(limit-p)/1457){
  B *r=(B *)(p+z.slot*1457);
  for(i=0;i<16&&r[i]==z.full[i];i++){}
  if(i==16&&r[1456]==z.full[400]){
   copy(z.record,z.full,401);offset=z.pan*360/65536;
   for(k=0;k<4;k++)for(i=0;i<96;i++)
    z.record[16+k*96+i]=r[16+k*360+offset+i*180/96];
   ok=1;
  }
 }
 N(0x4000172a,void (*)(void *))(f+11);return ok;
}
static void summary(const B *b,U *h,U *n,U *peak){
 U i,p;I x;*h=((U)b[0]<<8)|b[1];*n=*peak=0;
 for(i=2;i<506;i++){if(b[i])(*n)++;x=(signed char)b[i];p=x<0?(U)-x:(U)x;if(p>*peak)*peak=p;}
}
static int request_worker(Fn *fn){
 U before[7],after[7];B prepared[20];(void)fn;q.state=2;BARRIER();
 q.error=-1;
 if(metadata(q.slot,before)&&info_equal(q.info,before)){
  /* Stock first editor worker4001ae6e calls400128ca (opcode7) before
   *4001a752/40012942 (opcode8). Reuse BOTH stock acquisition operations.
   * Each copies its shared RX into private storage before unlocking.
   * Pair on every changed viewport: don't assume a global prepared-reader
   * lifetime across other native editor/recording users. Unchanged paint
   * still uses our completed viewport cache and performs no IO. */
  N(0x400128ca,void (*)(U *,void *))(q.desc,prepared);
  if(!metadata(q.slot,after)||!info_equal(q.info,after)){
   q.error=-2;BARRIER();q.state=3;return q.error;
  }
  N(0x40012942,void (*)(U *,U,U,U,void *))(q.desc,q.start,q.end,126,q.reply);
  summary(q.reply,&q.header,&q.nonzero,&q.peak);
  q.error=metadata(q.slot,after)&&info_equal(q.info,after)?0:-2;
 }
 BARRIER();q.state=3;return q.error;
}
static int request_manager(Fn *to,const Fn *from,int op){
 if(op==1||op==2){to->data[0]=from->data[0];to->data[1]=from->data[1];}
 return 0; /* Captures only static storage; nothing can outlive an allocation. */
}
static int submit_detail(U start,U end){
 Fn work={{0,0},(U)request_manager,(U)request_worker},done={{0,0},0,0};
 U i;
 q.view=(U)z.view;q.slot=z.slot;q.level=z.level;q.pan=z.pan;
 for(i=0;i<7;i++)q.info[i]=z.info[i];
 q.desc[0]=z.slot<<16;q.desc[1]=z.info[1];q.desc[2]=z.info[2];
 q.start=start;q.end=end;q.header=q.nonzero=q.peak=q.converted=q.hits=0;q.error=0;
 /* No paint-context RPC. All deep IO uses the stock first-worker sequence. */
 q.direct_header=q.direct_nonzero=q.direct_peak=0;
 BARRIER();q.state=1;
 if(!N(0x400f0c1c,B (*)(void *,const char *,Fn *,Fn *))(
    N(0x40198cf2,void *(*)(void))(),"Wave Detail",&work,&done)){
  q.error=-3;BARRIER();q.state=3;return 0;
 }
 return 1;
}
/* Exact stock editor contract (4001a63a/4001a752): absolute start AND end,
 * 126 columns, 506-byte reply. Two leading bytes, then signed extrema pairs
 * at 2+4*i (primary) and 4+4*i (secondary). This is verified by executing
 * the unmodified native editor parser, not inferred from a synthetic peer. */
static int detail(void){
 U i,j,k,stop,start,end,p;I a,b,c,d,x,y;
 if(z.cache_valid&&z.cache_level==z.level&&z.cache_pan==z.pan)return 1;
 if(z.level==1&&cached2())goto ready;
 start=frame(z.pan);end=frame(z.pan+span());if(end<=start)end=start+1;
 if(end>z.frames)end=z.frames;
 if(end<=start)return 0;
 if(q.state==1||q.state==2)return 2;
 if(q.state==3){
  BARRIER();
  if(q.view!=(U)z.view||q.slot!=z.slot||q.level!=z.level||q.pan!=z.pan||!info_equal(q.info,z.info)){
   q.state=0;return submit_detail(start,end),2;
  }
  if(q.error){
   /* A failed new acquisition cannot overwrite the last completed view. */
   q.state=0;z.cache_level=z.level;z.cache_pan=z.pan;z.cache_valid=1;return 1;
  }
  copy(z.reply,q.reply,506);q.state=0;
 }else return submit_detail(start,end),2;
 copy(z.record,z.full,401);
 for(i=0;i<96;i++){
  a=c=-128;b=d=127;stop=(i+1)*126/96;
  for(k=i*126/96;k<stop;k++){
   j=2+4*k;
   x=(signed char)z.reply[j];y=(signed char)z.reply[j+1];
   if(x>a)a=x;
   if(y>a)a=y;
   if(x<b)b=x;
   if(y<b)b=y;
   x=(signed char)z.reply[j+2];y=(signed char)z.reply[j+3];
   if(x>c)c=x;
   if(y>c)c=y;
   if(x<d)d=x;
   if(y<d)d=y;
  }
  z.record[16+i]=(B)(a>b?a:b);z.record[112+i]=(B)(a<b?a:b);
  z.record[208+i]=(B)(c>d?c:d);z.record[304+i]=(B)(c<d?c:d);
 }
 z.record[400]=(B)z.ch;
 q.converted=0;
 for(i=16;i<400;i++){x=(signed char)z.record[i];p=x<0?(U)-x:(U)x;if(p>q.converted)q.converted=p;}
ready:
 z.record_valid=1;z.record_level=z.level;z.record_pan=z.pan;
 z.cache_level=z.level;z.cache_pan=z.pan;z.cache_valid=1;return 1;
}
static U raw(void *v,U code){B locked=0;I n=N(0x4006aa32,I (*)(void *,U,B *))(v,code,&locked);return (U)clamp(n,0,30720);}
static void markers(void){
 U *w=z.copy.raw;U begin,len,loop=0,track,phase,sz=span();I a,b;
 if(z.kind==0){
  U ca=z.type==0?206:z.type==2?224:233;
  begin=raw(z.view,ca);len=raw(z.view,ca+1);if(z.type==0){loop=raw(z.view,208);loop=loop>256?loop-256:0;}
  w[1]=(U)xpos(begin*65536u/30720);w[2]=(U)xpos((begin+len>30720?30720:begin+len)*65536u/30720);
  w[3]=(U)xpos(loop*65536u/30720);
 }else if(z.kind==2){
  a=xpos(fraction(w[1],z.frames));b=xpos(fraction(w[2],z.frames));
  ((B *)w)[46]=((B *)w)[46]&&b>=0&&a<96;
  w[1]=clamp(a,0,96);w[2]=clamp(b,0,96);w[3]=96;
 }
 track=N(0x4004abb8,U (*)(void *))(*(void **)((B *)z.view+0x74));
 phase=N(0x400cd88a,U (*)(U))(track);
 w[z.kind==0?5:4]=(U)xpos(phase>256?65536:phase*256);
 (void)sz;
}
static void fill(Bitmap *b,I x,I y,I x2,I y2,I color){N(0x401131ae,void (*)(Bitmap *,I,I,I,I,I))(b,x,y,x2,y2,color);}
static void navigator(Bitmap *b,int ready){
 U i,lo=render_pan*68/65536,hi=(render_pan+(65536u>>render_level))*68/65536;I a,c;
 fill(b,25,33,120,41,0);
 N(0x40113dc8,int (*)(Bitmap *,void *,I,I,I,const char *,...))(b,(void *)0x44d2e6f4,25,34,0,"%uX%s",1u<<z.level,ready?"":"?");
 for(i=0;i<68;i++){
  a=(signed char)z.full[16+i*96/68];c=(signed char)z.full[112+i*96/68];
  if(z.ch){I d=(signed char)z.full[208+i*96/68],e=(signed char)z.full[304+i*96/68];if(d>a)a=d;if(e<c)c=e;}
  a=37+a*2/128;c=37+c*2/128;fill(b,52+i,c,52+i,a,1);
 }
 if(hi<=lo)hi=lo+1;
 if(hi>68)hi=68;
 if(lo>=68)lo=67;
 fill(b,52+lo,34,51+hi,34,1);fill(b,52+lo,40,51+hi,40,1);
 fill(b,52+lo,34,52+lo,40,1);fill(b,51+hi,34,51+hi,40,1);
}
U zoom_factory_for(void *w){
 if(z.rendering&&w==z.render_owner){if(z.level>=2)q.hits++;return (U)z.factory;}
 return N(0x401a04e4,U (*)(void))();
}
void zoom_draw(void *widget,Bitmap *b,U kind){
 U info[7],i,slot=*(U *)widget,offset=kind==0?0x1d0:kind==1?0x204:0x234,t;
 void *v=(B *)widget-offset;U fn=kind==0?0x4010ee64:kind==1?0x4010f342:0x4010f782;
 int ready;
 t=type(v);
 if(!applicable(t)||!metadata(slot,info)){
  if(z.view==v){z.pressed=z.cache_valid=z.record_valid=0;z.view=z.owner=0;}
  N(fn,void (*)(void *,Bitmap *))(widget,b);return;
 }
 if(z.owner!=widget||z.view!=v||z.slot!=slot||z.type!=t||!info_equal(z.info,info)){
  z.level=z.pan=z.pressed=z.long_done=z.cache_valid=z.record_valid=0;
 }
 z.owner=widget;z.view=v;z.slot=slot;z.type=t;z.kind=kind;copy(z.info,info,28);
 z.ch=((B *)info)[24]?1:0;z.frames=info[4]>>z.ch;
 if(!z.frames||!overview()){
  if(z.level)fill(b,25,8,120,32,0);
  else N(fn,void (*)(void *,Bitmap *))(widget,b);
  return;
 }
 long_check();ready=z.level?detail():1;
 if(!ready){z.level=z.pan=0;z.cache_valid=0;}
 copy(&z.copy,widget,52);
 /* Keep the original waveform x/width and all parameter widgets. Reserve
  * the top8 rows of its old32-row pane for a genuinely separate overview. */
 z.copy.raw[kind==0?10:9]=24;
 render_level=render_pan=0;
 if(z.level&&z.record_valid){
  render_level=z.record_level;render_pan=z.record_pan;
  markers();
  for(i=0;i<15;i++)z.factory[i]=0;
  z.factory[0]=0x401f15c0;z.factory[1]=(U)z.record-slot*401;
  z.factory[2]=z.factory[3]=(U)z.record+401;
  z.render_owner=&z.copy;z.rendering=1;if(z.level>=2)q.hits=0;
 }
 if(!z.level||z.record_valid)N(fn,void (*)(void *,Bitmap *))(&z.copy,b);
 else fill(b,25,8,120,32,0); /* Never draw a1x substitute for pending detail. */
 z.rendering=0;z.render_owner=0;
 ((B *)widget)[kind==0?49:45]=((B *)&z.copy)[kind==0?49:45];
 navigator(b,ready==1&&(!z.level||(z.record_valid&&z.record_level==z.level&&z.record_pan==z.pan)));
}
/* Called IN the native empty encoder-cell branches (40016f4c/40016f82),
 * before waveform drawing, with the stock slot index/x/y. No fake musical
 * parameter IDs, global descriptor edits, routing changes or later overlay.
 * Caption renderer/font exactly match40069d32..40069d54. */
int zoom_cell(void *view,Bitmap *b,U x,U y,U index){
 U t=type(view),which,level=0,slot;void *widget;U info[7];
 if(*(U *)((B *)view+0x90)!=1||!applicable(t))return 0;
 which=zoom_pan_encoder(view);if(!which||index+1!=which)return 0;
 widget=(B *)view+(t==0||t==2||t==3?0x1d0:t==1||t==4?0x204:0x234);
 slot=*(U *)widget;
 if(view==z.view&&t==z.type&&slot==z.slot&&metadata(slot,info)&&info_equal(z.info,info))level=z.level;
 N(0x40113fd4,int (*)(Bitmap *,void *,I,I,I,I,const char *,const char *,...))(
  b,(void *)0x402a7170,x+9,y,1,0,"XXXX","%s","ZOOM");
 /* Native top cells have a value row above their caption. Footer cells
  * have only the native caption row before the waveform pane starts;
  * retain that layout and the existing navigator's current-zoom value. */
 if(t>4&&y>=40)N(0x40113fd4,int (*)(Bitmap *,void *,I,I,I,I,const char *,const char *,...))(
  b,(void *)0x402a9be0,x+9,y+7,1,0,"XXXX","%uX",1u<<level);
 return 1;
}
/* Marker primitives remain native. Only waveform-pane horizontal clipping
 * and the grid family's exact rational boundary projection are adapted. */
static int point(I *x,int grid){
 U base=z.copy.raw[z.kind==0?7:6],n=z.copy.raw[1],index;
 if(!z.rendering)return 1;
 if(grid&&z.kind==1&&n&&n<=64){
  I rel=*x-(I)base;
  if(rel>=0&&rel<96){index=((U)rel*n+95)/96;*x=base+xpos(index*65536/n);}
 }
 return *x>=(I)base&&*x<(I)base+96;
}
void zoom_vline(void *b,I x,I y1,I y2,I color){if(point(&x,1))N(0x401129d2,void (*)(void *,I,I,I,I))(b,x,y1,y2,color);}
void zoom_line(void *b,I x,I y1,I y2,I color){if(point(&x,0))N(0x40112848,void (*)(void *,I,I,I,I))(b,x,y1,y2,color);}
void zoom_pixel(void *b,I x,I y,I color){if(point(&x,0))N(0x401124fc,void (*)(void *,I,I,I))(b,x,y,color);}
void zoom_rect(void *b,I x1,I y1,I x2,I y2,I color){
 if(z.rendering){
  U base=z.copy.raw[z.kind==0?7:6];
  if(z.kind==1){U n=z.copy.raw[1],idx=z.copy.raw[2],len=z.copy.raw[3];
   if(n&&n<=64){U end=idx+len+1;if(end>n)end=n;x1=base+xpos(idx*65536/n);x2=base+xpos(end*65536/n);}}
  if(x2<(I)base||x1>=(I)base+96)return;
  x1=clamp(x1,base,base+95);x2=clamp(x2,base,base+95);
 }
 N(0x401134c0,void (*)(void *,I,I,I,I,I))(b,x1,y1,x2,y2,color);
}
/* Test/read-only state access, no activation or alternate production path. */
void zoom_observe(U *out){out[0]=(U)z.view;out[1]=z.level;out[2]=z.pan;out[3]=span();out[4]=z.frames;out[5]=z.cache_valid;out[6]=z.pressed;out[7]=z.long_done;out[8]=(U)&z;}
void zoom_detail_observe(U *out){U i;const U *p=(const U *)&q;for(i=0;i<28;i++)out[i]=p[i];}
void zoom_render_observe(U *out){out[0]=z.record_valid;out[1]=z.record_level;out[2]=z.record_pan;out[3]=render_level;out[4]=render_pan;}
