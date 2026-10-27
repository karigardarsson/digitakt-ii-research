/* DT2 1.17 SourcePageView::vfunc_45. The stock caller already resolves the
 * displayed value (including editing parameter locks) and all selection flags.
 * Keep the original renderer for labels/highlights, then replace only its glyph
 * rectangle. No globals, allocation, Sound writes, or DSP accesses.
 *
 * Verified callers: SourcePageView::vfunc_4 0x40016ff8 and the generic
 * ParameterPageView::vfunc_4 0x4006b24c. Source vtable only is redirected.
 * Bitmap coordinates have row zero at the bottom of the screen.
 */
typedef unsigned int u32;
typedef struct {
    u32 vtable, width, height, stride;
    u32 *pixels;
    u32 aux, owns;
} Bitmap;

extern void plaits_stock_widget(void *, Bitmap *, int, int, int, int,
                               int, int, int, int, int);
extern void plaits_pixel(Bitmap *, int, int, int);
extern void plaits_fill(Bitmap *, int, int, int, int, int);
extern int plaits_text(Bitmap *, void *, int, int, int, const char *, ...);
extern void plaits_model_short(int, char *);

void plaits_ui_draw(void *view, Bitmap *b, int x, int y, int code, int raw,
                    int a7, int a8, int a9, int a10, int a11) {
    int value, i, n;
    char text[6];
    void *font = (void *)0x44d2e70c;
    plaits_stock_widget(view,b,x,y,code,raw,a7,a8,a9,a10,a11);
    if (code < 275 || code > 279 || b == (void *)0 ||
        b->width != 128 || b->height != 64 || b->stride != 2 ||
        b->pixels == (void *)0 || x < 3 || x > 106 || y < -2 || y > 38)
        return;
    if (raw < 0) raw = 0;
    if (raw > 0x7f00) raw = 0x7f00;
    value = raw >> 8;
    /* Stock glyph bounds, exclusive of the label and lock/selection frame. */
    plaits_fill(b,x-3,y+7,x+21,y+24,0);
    if (code == 275) {
        if (raw > 0x1800) raw = 0x1800;
        plaits_model_short(raw,text);
        plaits_text(b,font,x+9,y+13,2,"%s",text);
        /* Full-width rail; the name disambiguates all 25 positions. */
        n = ((raw >> 8) * 20 + 12) / 24;
        plaits_fill(b,x-1,y+23,x+19,y+23,1);
        plaits_fill(b,x-1+n,y+21,x-1+n,y+22,1);
    } else {
        /* One 23x17 outline, a five-pixel-high value, and a separated
         * six-segment indicator. All marks remain inside the old glyph. */
        plaits_fill(b,x-2,y+8,x+20,y+8,1);
        plaits_fill(b,x-2,y+24,x+20,y+24,1);
        plaits_fill(b,x-2,y+8,x-2,y+24,1);
        plaits_fill(b,x+20,y+8,x+20,y+24,1);
        /* DX7 patch names remain in the native label below this box. */
        plaits_text(b,font,x+9,y+16,2,"%d",value);
        n = (value * 6 + 63) / 127;
        for (i=0;i<6;++i) {
            int left=x+1+3*i;
            plaits_fill(b,left,y+10,left+1,y+10,1);
            if (code != 279 && i<n)
                plaits_fill(b,left,y+11,left+1,y+12,1);
        }
        if (code == 279) {
            /* OUT at left, AUX at right. A fixed center tick plus a movable
             * two-row cursor makes center and both endpoints unambiguous. */
            n = (value * 16 + 63) / 127;
            plaits_fill(b,x+9,y+9,x+9,y+13,1);
            plaits_fill(b,x+1+n,y+11,x+1+n,y+12,1);
        }
    }
}
