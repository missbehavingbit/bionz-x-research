/* Unit tests and 24 MP benchmark for the C99 / ARMv7 film_post kernel. */
#define _POSIX_C_SOURCE 200809L
#include "film_post.h"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static double elapsed_ms(struct timespec a, struct timespec b) {
    return (double)(b.tv_sec - a.tv_sec) * 1000.0 + (double)(b.tv_nsec - a.tv_nsec) / 1e6;
}

static void test_tile_determinism_and_range(void) {
    uint8_t tile_a[FILM_POST_TILE_SIZE];
    uint8_t tile_b[FILM_POST_TILE_SIZE];
    film_post_init_tile(tile_a);
    film_post_init_tile(tile_b);
    assert(memcmp(tile_a, tile_b, FILM_POST_TILE_SIZE) == 0);

    uint64_t sum = 0;
    uint8_t min_v = 255, max_v = 0;
    for (size_t i = 0; i < FILM_POST_TILE_SIZE; ++i) {
        sum += tile_a[i];
        if (tile_a[i] < min_v) min_v = tile_a[i];
        if (tile_a[i] > max_v) max_v = tile_a[i];
    }
    double mean = (double)sum / (double)FILM_POST_TILE_SIZE;
    assert(min_v >= 24 && max_v <= 232);
    assert(mean >= 126.0 && mean <= 130.0);
    printf("PASS tile: min=%u max=%u mean=%.2f\n", min_v, max_v, mean);
}

static void test_disabled_noop(void) {
    uint8_t tile[FILM_POST_TILE_SIZE];
    film_post_init_tile(tile);

    FilmHalationParams hp;
    FilmGrainParams gp;
    film_post_default_halation(&hp);
    film_post_default_grain(&gp);

    FilmPostPrepared cfg;
    film_post_prepare_halation(&hp, &cfg.halation);
    film_post_prepare_grain(&gp, &cfg.grain);

    enum { W = 64, H = 64 };
    uint8_t *buf = (uint8_t *)malloc(W * H * 4);
    uint8_t *orig = (uint8_t *)malloc(W * H * 4);
    assert(buf && orig);
    for (size_t i = 0; i < W * H * 4; ++i) {
        buf[i] = (uint8_t)((i * 37u + 13u) & 255u);
    }
    memcpy(orig, buf, W * H * 4);
    assert(film_post_apply_rgba(buf, W, H, &cfg, tile, NULL, 0) == 0);
    assert(memcmp(buf, orig, W * H * 4) == 0);
    free(buf);
    free(orig);
    printf("PASS disabled noop\n");
}

static void test_halation_and_mask(void) {
    uint8_t tile[FILM_POST_TILE_SIZE];
    film_post_init_tile(tile);

    FilmHalationParams hp;
    film_post_default_halation(&hp);
    hp.enabled = 1;

    FilmGrainParams gp;
    film_post_default_grain(&gp);

    FilmPostPrepared cfg;
    film_post_prepare_halation(&hp, &cfg.halation);
    film_post_prepare_grain(&gp, &cfg.grain);

    enum { W = 96, H = 96 };
    uint8_t *base = (uint8_t *)malloc(W * H * 4);
    uint8_t *full = (uint8_t *)malloc(W * H * 4);
    uint8_t *masked = (uint8_t *)malloc(W * H * 4);
    assert(base && full && masked);

    for (int y = 0; y < H; ++y) {
        for (int x = 0; x < W; ++x) {
            size_t p = (size_t)(y * W + x) * 4u;
            uint8_t v = (x >= 36 && x < 60 && y >= 36 && y < 60) ? 255u : 0u;
            base[p] = v;
            base[p + 1] = v;
            base[p + 2] = v;
            base[p + 3] = 255u;
        }
    }
    memcpy(full, base, W * H * 4);
    assert(film_post_apply_rgba(full, W, H, &cfg, tile, NULL, 0) == 0);

    size_t edge = (size_t)(48 * W + 35) * 4u;
    uint8_t r = full[edge], g = full[edge + 1], b = full[edge + 2];
    assert(r > 10 && r > g && g > b);

    hp.mask_mode = 1;
    film_post_prepare_halation(&hp, &cfg.halation);
    memcpy(masked, base, W * H * 4);
    assert(film_post_apply_rgba(masked, W, H, &cfg, tile, NULL, 0) == 0);
    assert(masked[0] == 0 && masked[1] == 0 && masked[2] == 0);
    size_t center = (size_t)(48 * W + 48) * 4u;
    assert(masked[center] == 0 && masked[center + 1] == 0 && masked[center + 2] == 0);
    assert(masked[edge] > 10);

    free(base);
    free(full);
    free(masked);
    printf("PASS halation edge=(%u,%u,%u)\n", r, g, b);
}

static void test_grain_and_ycbcr422(void) {
    uint8_t tile[FILM_POST_TILE_SIZE];
    film_post_init_tile(tile);

    FilmHalationParams hp;
    film_post_default_halation(&hp);
    hp.enabled = 1;

    FilmGrainParams gp;
    film_post_default_grain(&gp);
    gp.enabled = 1;
    gp.seed = 42u;

    FilmPostPrepared cfg;
    film_post_prepare_halation(&hp, &cfg.halation);
    film_post_prepare_grain(&gp, &cfg.grain);

    enum { W = 128, H = 96 };
    uint8_t *y = (uint8_t *)malloc(W * H);
    uint8_t *cbcr = (uint8_t *)malloc(W * H);
    assert(y && cbcr);
    memset(y, 16, W * H);
    memset(cbcr, 128, W * H);
    for (int row = 32; row < 64; ++row) {
        for (int col = 48; col < 80; ++col) {
            y[row * W + col] = 250u;
        }
    }

    size_t sc_bytes = film_post_scratch_bytes(W, H);
    void *scratch = malloc(sc_bytes);
    assert(scratch);

    /* Verify Halation in YCbCr422 produces warm red-orange halo (Cr > 128, Cb < 128) right outside col=79. */
    cfg.grain.enabled = 0;
    assert(film_post_apply_ycbcr422(y, W, cbcr, W, W, H, &cfg, tile, scratch, sc_bytes) == 0);
    int edge_cx = 80;
    uint8_t edge_cb = cbcr[48 * W + edge_cx];
    uint8_t edge_cr = cbcr[48 * W + edge_cx + 1];
    assert(edge_cr > 128 && edge_cb < 128);

    /* Now verify combined Halation -> Film Grain in YCbCr422 on a mid-gray background. */
    memset(y, 128, W * H);
    memset(cbcr, 128, W * H);
    for (int row = 32; row < 64; ++row) {
        for (int col = 48; col < 80; ++col) {
            y[row * W + col] = 250u;
        }
    }
    cfg.grain.enabled = 1;
    assert(film_post_apply_ycbcr422(y, W, cbcr, W, W, H, &cfg, tile, scratch, sc_bytes) == 0);
    int diffs = 0;
    for (int row = 0; row < 24; ++row) {
        for (int col = 0; col < 32; ++col) {
            if (y[row * W + col] != 128) diffs++;
        }
    }
    assert(diffs > 100);

    /* NV12 path with monochrome grain (color=0). */
    gp.color = 0.0f;
    film_post_prepare_grain(&gp, &cfg.grain);
    uint8_t *cbcr420 = (uint8_t *)malloc(W * (H / 2));
    assert(cbcr420);
    memset(y, 120, W * H);
    memset(cbcr420, 128, W * (H / 2));
    assert(film_post_apply_nv12(y, W, cbcr420, W, W, H, &cfg, tile, scratch, sc_bytes) == 0);

    free(y);
    free(cbcr);
    free(cbcr420);
    free(scratch);
    printf("PASS ycbcr422 & nv12 edge Cb=%u Cr=%u\n", edge_cb, edge_cr);
}

static void bench_24mp(void) {
    uint8_t tile[FILM_POST_TILE_SIZE];
    film_post_init_tile(tile);

    FilmHalationParams hp;
    film_post_default_halation(&hp);
    hp.enabled = 1;

    FilmGrainParams gp;
    film_post_default_grain(&gp);
    gp.enabled = 1;

    FilmPostPrepared cfg;
    film_post_prepare_halation(&hp, &cfg.halation);
    film_post_prepare_grain(&gp, &cfg.grain);

    enum { W = 6000, H = 4000 };
    size_t sc_bytes = film_post_scratch_bytes(W, H);
    void *scratch = malloc(sc_bytes);
    uint8_t *rgba = (uint8_t *)malloc((size_t)W * (size_t)H * 4u);
    uint8_t *y = (uint8_t *)malloc((size_t)W * (size_t)H);
    uint8_t *cbcr = (uint8_t *)malloc((size_t)W * (size_t)H);
    assert(scratch && rgba && y && cbcr);

    memset(rgba, 64, (size_t)W * (size_t)H * 4u);
    memset(y, 64, (size_t)W * (size_t)H);
    memset(cbcr, 128, (size_t)W * (size_t)H);
    for (int row = 1500; row < 2000; ++row) {
        for (int col = 2500; col < 3000; ++col) {
            size_t p = ((size_t)row * W + col) * 4u;
            rgba[p] = rgba[p + 1] = rgba[p + 2] = 255u;
            y[(size_t)row * W + col] = 255u;
        }
    }

    struct timespec t0, t1, t2;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    assert(film_post_apply_rgba(rgba, W, H, &cfg, tile, scratch, sc_bytes) == 0);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    assert(film_post_apply_ycbcr422(y, W, cbcr, W, W, H, &cfg, tile, scratch, sc_bytes) == 0);
    clock_gettime(CLOCK_MONOTONIC, &t2);

    printf(
        "BENCH 24MP (6000x4000, scratch=%.2f MiB): RGBA=%.1f ms, MainYC422=%.1f ms\n",
        (double)sc_bytes / (1024.0 * 1024.0),
        elapsed_ms(t0, t1),
        elapsed_ms(t1, t2)
    );

    free(scratch);
    free(rgba);
    free(y);
    free(cbcr);
}

int main(void) {
    test_tile_determinism_and_range();
    test_disabled_noop();
    test_halation_and_mask();
    test_grain_and_ycbcr422();
    bench_24mp();
    printf("ALL FILM_POST C TESTS PASSED\n");
    return 0;
}
