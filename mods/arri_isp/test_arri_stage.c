#include "arri_stage.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

static void verify(const char *path, enum arri_preset_id id) {
    uint8_t blob[ARRI_A6LT_BYTES];
    struct arri_loaded loaded = {0};
    struct arri_tables output;
    struct arri_tables factory;
    struct arri_pp_state target = {1, 2, 2, 1, 1, 1, 1, 1, 1, 0};
    FILE *fp = fopen(path, "rb");
    assert(fp && fread(blob, 1, sizeof(blob), fp) == sizeof(blob));
    assert(fgetc(fp) == EOF && !ferror(fp) && fclose(fp) == 0);

    assert(arri_load(&loaded, id, blob, sizeof(blob)));
    assert(loaded.id == id);
    assert(memcmp(loaded.tables.gamma, blob + 32, 192) == 0);
    assert(memcmp(loaded.tables.matrix, blob + 224, 18) == 0);
    assert(memcmp(loaded.tables.esram, blob + 244, 1620) == 0);
    struct arri_loaded good = loaded;
    assert(!arri_load(&loaded, id, blob, sizeof(blob) - 1));
    blob[95] ^= 1;
    assert(!arri_load(&loaded, id, blob, sizeof(blob)));
    blob[95] ^= 1;
    blob[8] = 0;
    assert(!arri_load(&loaded, id, blob, sizeof(blob)));
    assert(loaded.id == good.id && memcmp(&loaded.tables, &good.tables, sizeof(good.tables)) == 0);

    memset(&factory, 0xab, sizeof(factory));
    for (unsigned i = 0; i < 4; ++i) {
        output = factory;
        assert(arri_stage(&output, &loaded, &target) == ARRI_STAGED);
        assert(memcmp(&output, &loaded.tables, sizeof(output)) == 0);
    }

    for (size_t i = 0; i < sizeof(target); ++i) {
        struct arri_pp_state bypass = target;
        ((uint8_t *)&bypass)[i] = 0;
        if (i == offsetof(struct arri_pp_state, playback))
            bypass.playback = 1;
        output = factory;
        assert(arri_stage(&output, &loaded, &bypass) == ARRI_BYPASS);
        assert(memcmp(&output, &factory, sizeof(output)) == 0);
    }
    loaded.id = ARRI_NONE;
    assert(arri_stage(&output, &loaded, &target) == ARRI_BYPASS);
    assert(memcmp(&output, &factory, sizeof(output)) == 0);
}

int main(int argc, char **argv) {
    assert(argc == 3);
    verify(argv[1], ARRI_709);
    verify(argv[2], ARRI_AWG3);
    puts("Both exported presets passed validation, conditional staging and factory bypass.");
    return 0;
}
