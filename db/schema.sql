-- BuildCheck parts catalog schema (PostgreSQL).
-- Re-runnable: drops everything first, in dependency order.
--
-- Normalization conventions (enforced by engine/normalize.py in the loaders):
--   sockets       LGA1851, LGA1700, AM5, AM4   (uppercase, no spaces)
--   memory type   DDR4 / DDR5
--   speeds        integer MT/s
--   form factors  ATX, mATX, ITX, E-ATX

DROP TABLE IF EXISTS builds CASCADE;
DROP TABLE IF EXISTS board_qvl CASCADE;
DROP TABLE IF EXISTS board_cpu_support CASCADE;
DROP TABLE IF EXISTS coolers CASCADE;
DROP TABLE IF EXISTS psus CASCADE;
DROP TABLE IF EXISTS cases CASCADE;
DROP TABLE IF EXISTS gpus CASCADE;
DROP TABLE IF EXISTS memory_kits CASCADE;
DROP TABLE IF EXISTS motherboards CASCADE;
DROP TABLE IF EXISTS cpus CASCADE;

-- Every part table starts with the same five columns:
--   id, brand, model, source_url, review_status, created_at

CREATE TABLE cpus (
  id SERIAL PRIMARY KEY,
  brand TEXT NOT NULL,
  model TEXT NOT NULL,
  source_url TEXT,
  review_status TEXT NOT NULL DEFAULT 'approved'
    CHECK (review_status IN ('approved','needs_review','rejected')),
  created_at TIMESTAMPTZ DEFAULT now(),
  socket TEXT,
  tdp_w INT,
  has_igpu BOOLEAN,
  mem_type TEXT,
  max_mem_speed INT
);

CREATE TABLE motherboards (
  id SERIAL PRIMARY KEY,
  brand TEXT NOT NULL,
  model TEXT NOT NULL,
  source_url TEXT,
  review_status TEXT NOT NULL DEFAULT 'approved'
    CHECK (review_status IN ('approved','needs_review','rejected')),
  created_at TIMESTAMPTZ DEFAULT now(),
  revision TEXT,
  socket TEXT,
  chipset TEXT,
  form_factor TEXT,
  mem_type TEXT,
  mem_slots INT,
  max_mem_gb INT,
  max_mem_speed INT,
  shipped_bios TEXT,
  bios_flashback BOOLEAN
);

CREATE TABLE memory_kits (
  id SERIAL PRIMARY KEY,
  brand TEXT NOT NULL,
  model TEXT NOT NULL,
  source_url TEXT,
  review_status TEXT NOT NULL DEFAULT 'approved'
    CHECK (review_status IN ('approved','needs_review','rejected')),
  created_at TIMESTAMPTZ DEFAULT now(),
  mem_type TEXT,
  speed INT,
  modules INT,
  gb_per_module INT,
  form_factor TEXT DEFAULT 'DIMM'
);

CREATE TABLE gpus (
  id SERIAL PRIMARY KEY,
  brand TEXT NOT NULL,
  model TEXT NOT NULL,
  source_url TEXT,
  review_status TEXT NOT NULL DEFAULT 'approved'
    CHECK (review_status IN ('approved','needs_review','rejected')),
  created_at TIMESTAMPTZ DEFAULT now(),
  length_mm INT,
  tbp_w INT,
  pcie_8pin_required INT DEFAULT 0,
  requires_12v_2x6 BOOLEAN DEFAULT false
);

CREATE TABLE cases (
  id SERIAL PRIMARY KEY,
  brand TEXT NOT NULL,
  model TEXT NOT NULL,
  source_url TEXT,
  review_status TEXT NOT NULL DEFAULT 'approved'
    CHECK (review_status IN ('approved','needs_review','rejected')),
  created_at TIMESTAMPTZ DEFAULT now(),
  supported_form_factors TEXT[],
  max_gpu_mm INT,
  max_cooler_mm INT,
  psu_form_factor TEXT,
  max_radiator_mm INT            -- 0 if the case can't take a radiator
);

CREATE TABLE psus (
  id SERIAL PRIMARY KEY,
  brand TEXT NOT NULL,
  model TEXT NOT NULL,
  source_url TEXT,
  review_status TEXT NOT NULL DEFAULT 'approved'
    CHECK (review_status IN ('approved','needs_review','rejected')),
  created_at TIMESTAMPTZ DEFAULT now(),
  watts INT,
  form_factor TEXT,
  pcie_8pin INT,
  has_12v_2x6 BOOLEAN
);

CREATE TABLE coolers (
  id SERIAL PRIMARY KEY,
  brand TEXT NOT NULL,
  model TEXT NOT NULL,
  source_url TEXT,
  review_status TEXT NOT NULL DEFAULT 'approved'
    CHECK (review_status IN ('approved','needs_review','rejected')),
  created_at TIMESTAMPTZ DEFAULT now(),
  cooler_type TEXT CHECK (cooler_type IN ('air','aio')),
  height_mm INT,                 -- air coolers; NULL for AIO
  radiator_mm INT,               -- AIO; NULL for air
  sockets TEXT[]
);

-- ------------------------------------------------------------- join tables

CREATE TABLE board_cpu_support (
  board_id INT REFERENCES motherboards(id) ON DELETE CASCADE,
  cpu_id   INT REFERENCES cpus(id) ON DELETE CASCADE,
  min_bios TEXT,                 -- NULL = works on shipped BIOS
  source_url TEXT,
  review_status TEXT NOT NULL DEFAULT 'approved'
    CHECK (review_status IN ('approved','needs_review','rejected')),
  PRIMARY KEY (board_id, cpu_id)
);

CREATE TABLE board_qvl (
  board_id INT REFERENCES motherboards(id) ON DELETE CASCADE,
  kit_id   INT REFERENCES memory_kits(id) ON DELETE CASCADE,
  source_url TEXT,
  review_status TEXT NOT NULL DEFAULT 'approved'
    CHECK (review_status IN ('approved','needs_review','rejected')),
  PRIMARY KEY (board_id, kit_id)
);

-- ------------------------------------------------------------- saved builds

CREATE TABLE builds (
  id SERIAL PRIMARY KEY,
  name TEXT,
  cpu_id INT REFERENCES cpus(id),
  board_id INT REFERENCES motherboards(id),
  kit_id INT REFERENCES memory_kits(id),
  kit_qty INT DEFAULT 1,
  gpu_id INT REFERENCES gpus(id),          -- nullable (iGPU only)
  case_id INT REFERENCES cases(id),
  psu_id INT REFERENCES psus(id),
  cooler_id INT REFERENCES coolers(id),    -- nullable (stock cooler)
  created_at TIMESTAMPTZ DEFAULT now()
);
