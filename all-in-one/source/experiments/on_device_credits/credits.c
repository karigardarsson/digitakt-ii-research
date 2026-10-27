/* Original Credits UI glue, 2026. Derived firmware: digikit by Em D.
 * AGPL-3.0-or-later; original NOTICE remains applicable. Native OS1.17 only.
 * No audio, persistent state, timers or background callbacks.
 */
typedef unsigned int U;
typedef struct { U raw,control; } Pair;
typedef struct { U data[2]; U manager,invoke; } Fn;
typedef struct { U vt,w,h,stride,pixels,aux,owns; } Bitmap;
extern void *alloc(U);
extern void str_ctor(void *,const char *,void *);
extern void str_dtor(void *);
extern void menu_ctor(void *,const char *,int,void *,void *,int);
extern void item_ctor(void *,const char *,Fn *,const Bitmap *,Fn *);
extern void menu_append(void *,void *);
extern void pair_drop(void *);
extern int menu_show(void *,Pair *);
extern void adopt(void *,void *,Pair *);
extern void fill(Bitmap *,int,int,int,int,int);
extern int text(Bitmap *,void *,int,int,int,const char *,...);
extern void dirty(void *);
const char credits_attribution[]="digikit by Em D - https://github.com/ElekLabs/digikit";
const char credits_origin[]="Modified firmware derived from digikit - https://github.com/ElekLabs/digikit";
static const Bitmap blank;
static int manager(Fn *to,const Fn *from,int op) {
  /* Small trivially-copyable closure: native std::function operation2 copies. */
  if(op==2){to->data[0]=from->data[0];to->data[1]=from->data[1];}
  return 0;
}
static void add(void *menu,const char *label,void (*action)(Fn *),void *ctx) {
  Fn empty={{0,0},0,0},fn={{(U)ctx,0},action?(U)manager:0,(U)action};
  void *item=alloc(84);
  item_ctor(item,label,&fn,&blank,&empty);
  menu_append(menu,item);
}
static const char *const pages[3][5]={
  {"DT2 ALL-IN-ONE","Modified; derived from","digikit by Em D -","https://github.com/","ElekLabs/digikit"},
  {"digikit (C) 2026 Em D","AGPL-3.0-or-later","No warranty.","You may convey under","the AGPL; see LICENSE."},
  {"Source + LICENSE:","included in package.","Original: see page 1.","Not endorsed by Em D","or Elektron."}
};
void credits_draw(void *view,Bitmap *b) {
  void *menu=(char *)view-56;
  U *vt=*(U **)menu;
  int page=((int (*)(void *))vt[9])(menu),i;
  if(page<0 || page>2)page=0;
  fill(b,0,0,127,63,0);
  text(b,(void *)0x44d2e70c,2,56,0,"CREDITS / ABOUT %d/3",page+1);
  for(i=0;i<5;i++)text(b,(void *)0x44d2e70c,2,46-8*i,0,"%s",pages[page][i]);
  text(b,(void *)0x44d2e70c,2,2,0,"UP/DOWN: PAGE  NO: BACK");
}
void credits_open(Fn *fn) {
  void *parent=(void *)fn->data[0],*menu=alloc(508);
  U title=0,i,*vt; char allocator=0; Pair child;
  str_ctor(&title,"",&allocator);
  menu_ctor(menu,"DigiKitCredits",1,&title,0,1);
  str_dtor(&title);
  /* Private copy retains native RTTI, destructor thunks, input and lifecycle. */
  vt=(U *)((char *)menu+428);
  for(i=0;i<20;i++)vt[i]=((U *)0x4021dd90)[i];
  vt[6]=(U)credits_draw;
  *(U *)((char *)menu+56)=(U)(vt+2);
  add(menu,"1",0,0);add(menu,"2",0,0);add(menu,"3",0,0);
  adopt(parent,(char *)menu+56,&child);
  menu_show(parent,&child);
  pair_drop(&child.control);
}
void credits_append(void *menu) {add(menu,"CREDITS / ABOUT",credits_open,menu);}
