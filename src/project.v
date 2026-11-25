/*
 * Centinela-CO: dual-channel fail-safe decision core for gas space heaters.
 *
 * Reads a carbon-monoxide concentration that an external front end has already
 * digitised (8 bits, 2 ppm per LSB), integrates the dose above a 40 ppm
 * baseline and removes the permission to keep the gas valve open when the
 * dose reaches 2880 ppm*min. With one sample per second that single number
 * places the trip inside the UL 2034 response windows: 8 min at 400 ppm,
 * 26 min at 150 ppm and 96 min at 70 ppm, and never at 30 ppm.
 *
 * Safety structure (didactic, NOT a certified safety component):
 *   - two diverse channels (true and one's-complement arithmetic) compared
 *     every clock; any disagreement latches FAULT;
 *   - the valve permission is dynamic: two quadrature square waves that only
 *     keep toggling while BOTH channels independently agree it is safe;
 *   - start-up self-test proves that the trip path and the comparator work
 *     before the valve can be opened for the first time;
 *   - windowed watchdog on the samples really taken (0.75 s to 1.5 s);
 *   - 64 clean samples are required before every first permission, and the
 *     trip is latched until a manual re-arm with the dose below one half.
 *
 * Copyright (c) 2026 Equipo UTCJ - IEEE Open Silicon Bootcamp 2026
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

module tt_um_utcj_centinela_co (
    input  wire [7:0] ui_in,    // CO reading, 2 ppm per LSB (0..510 ppm)
    output wire [7:0] uo_out,   // [6:0] 7-segment a..g, [7] dp = valve permitted
    input  wire [7:0] uio_in,   // [0] strobe  [1] rearm  [2] demo  [3] chain_ok
    output wire [7:0] uio_out,  // [4] valve_a [5] valve_b [6] alarm [7] fault
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered
    input  wire       clk,      // 32768 Hz
    input  wire       rst_n     // reset_n - low to reset
);

  // ------------------------------------------------------------------
  // Constants (2 ppm per LSB, one sample per second)
  // ------------------------------------------------------------------
  localparam [7:0]  C0   = 8'd20;        // 40 ppm baseline: below it the dose decays
  localparam [16:0] TH   = 17'd86400;    // trip dose = 2880 ppm*min above baseline
  localparam [16:0] TH2  = 17'd43200;    // half dose: re-arm limit
  localparam [16:0] MAXV = 17'h1FFFF;
  localparam [16:0] BLO  = MAXV - TH;    // channel B value that means "trip"
  localparam [16:0] BLO2 = MAXV - TH2;   // channel B value that means "half dose"

  localparam S_BT = 0;     // self-test 1: both channels must reach the trip dose
  localparam S_BC = 1;     // self-test 2: the comparator must see a forced mismatch
  localparam S_STBY = 2;   // valve closed, waiting for clean air and a manual re-arm
  localparam S_RUN = 3;    // valve permitted
  localparam S_TRIP = 4;   // dose reached: valve closed, alarm on
  localparam S_FAULT = 5;  // internal or sensor fault: valve closed until reset

  // ------------------------------------------------------------------
  // Reset: asynchronous assert (outputs go safe without a clock),
  // synchronous release.
  // ------------------------------------------------------------------
  reg [1:0] rst_sync;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) rst_sync <= 2'b00;
    else        rst_sync <= {rst_sync[0], 1'b1};
  wire rstn = rst_sync[1];

  // ------------------------------------------------------------------
  // Time base
  // ------------------------------------------------------------------
  reg [12:0] pre;
  always @(posedge clk or negedge rstn)
    if (!rstn) pre <= 13'd0;
    else       pre <= pre + 13'd1;

  wire t64    = &pre[8:0];  // 64 Hz tick
  wire t_ring = &pre[2:0];  // 4096 Hz tick for the valve ring

  // ------------------------------------------------------------------
  // Input synchronisers
  // ------------------------------------------------------------------
  reg [7:0] ui_s;
  reg [2:0] stb_s, arm_s;
  reg [1:0] chn_s, dem_s;
  always @(posedge clk or negedge rstn)
    if (!rstn) begin
      ui_s  <= 8'h00;
      stb_s <= 3'b000;
      arm_s <= 3'b000;
      chn_s <= 2'b00;
      dem_s <= 2'b00;
    end else begin
      ui_s  <= ui_in;
      stb_s <= {stb_s[1:0], uio_in[0]};
      arm_s <= {arm_s[1:0], uio_in[1]};
      dem_s <= {dem_s[0], uio_in[2]};
      chn_s <= {chn_s[0], uio_in[3]};
    end

  wire stb_rise = stb_s[1] & ~stb_s[2];
  wire arm_rise = arm_s[1] & ~arm_s[2];
  wire chain_ok = chn_s[1];

  // A re-arm press counts only if the button has been quiet for 0.5 s,
  // so contact bounce cannot act as two presses.
  reg  [4:0] arm_hold;
  wire       arm_ok = arm_rise & (arm_hold == 5'd0);
  always @(posedge clk or negedge rstn)
    if (!rstn)                        arm_hold <= 5'd0;
    else if (arm_rise)                arm_hold <= 5'd31;
    else if (t64 & (arm_hold != 5'd0)) arm_hold <= arm_hold - 5'd1;

  // ------------------------------------------------------------------
  // State register (one-hot, with an illegal-state check)
  // ------------------------------------------------------------------
  (* fsm_encoding = "none" *) reg [5:0] st;
  reg [8:0] bcnt;
  reg       demo_q;

  wire in_bist = st[S_BT] | st[S_BC];
  wire onehot_ok = (st == 6'b000001) | (st == 6'b000010) | (st == 6'b000100) |
                   (st == 6'b001000) | (st == 6'b010000) | (st == 6'b100000);

  // ------------------------------------------------------------------
  // Sampling. Normal mode: one sample per external strobe.
  // Demo mode: one sample per 64 Hz tick (time runs 64 times faster).
  // ------------------------------------------------------------------
  reg       upd_qa, upd_qbn; // one update flag per channel (B's is active low)
  reg [7:0] co_a;            // channel A copy of the reading
  reg [7:0] co_bn;           // channel B copy, stored inverted

  wire samp = ~in_bist & (demo_q ? t64 : stb_rise);

  always @(posedge clk or negedge rstn)
    if (!rstn) begin
      co_a    <= 8'h00;
      co_bn   <= 8'hFF;
      upd_qa  <= 1'b0;
      upd_qbn <= 1'b1;
    end else begin
      upd_qa  <= samp;
      upd_qbn <= ~samp;
      if (samp) begin
        co_a  <= ui_s;
        co_bn <= ~ui_s;
      end
    end

  // The watchdog is fed by the fact that BOTH channels took a sample.
  wire kick = upd_qa & ~upd_qbn;

  // During the self-test the inputs are forced and every clock is a sample.
  wire       upd_a = in_bist | upd_qa;
  wire       upd_b = in_bist | ~upd_qbn;
  wire [7:0] a_in  = in_bist ? 8'hFF : co_a;
  wire [7:0] b_inn = st[S_BT] ? 8'h00 : (st[S_BC] ? 8'hFF : co_bn);
  wire       clr;

  // ------------------------------------------------------------------
  // Channel A: dose counts up from 0 and holds once the trip dose is reached
  // ------------------------------------------------------------------
  reg  [16:0] acc_a;
  wire        trip_a = (acc_a >= TH);
  wire        warn_a = (acc_a >= TH2);
  wire        a_hi   = (a_in >= C0);
  wire [7:0]  a_up   = a_in - C0;
  wire [7:0]  a_dn   = (C0 - a_in + 8'd1) >> 1;          // decays at half rate, never 0
  wire [8:0]  a_dlt  = a_hi ? {1'b0, a_up} : (9'd0 - {1'b0, a_dn});
  wire [17:0] a_nxt  = {1'b0, acc_a} + {{9{a_dlt[8]}}, a_dlt};

  always @(posedge clk or negedge rstn)
    if (!rstn)    acc_a <= 17'd0;
    else if (clr) acc_a <= 17'd0;
    else if (upd_a & ~(a_hi & trip_a))                   // hold once tripped
      acc_a <= a_nxt[17] ? 17'd0 : a_nxt[16:0];          // never below zero

  // ------------------------------------------------------------------
  // Channel B: same dose held as its one's complement (counts down from
  // MAXV to BLO). Different arithmetic and different registers; its copy of
  // the reading is stored inverted.
  // ------------------------------------------------------------------
  reg  [16:0] acc_b;
  wire        trip_b = (acc_b <= BLO);
  wire        warn_b = (acc_b <= BLO2);
  wire [7:0]  b_in   = ~b_inn;
  wire        b_hi   = (b_in >= C0);
  wire [7:0]  b_up   = b_in - C0;
  wire [7:0]  b_dn   = (C0 - b_in + 8'd1) >> 1;
  wire [8:0]  b_dlt  = b_hi ? {1'b0, b_up} : (9'd0 - {1'b0, b_dn});
  wire [17:0] b_nxt  = {1'b0, acc_b} - {{9{b_dlt[8]}}, b_dlt};

  always @(posedge clk or negedge rstn)
    if (!rstn)    acc_b <= MAXV;
    else if (clr) acc_b <= MAXV;
    else if (upd_b & ~(b_hi & trip_b))
      acc_b <= b_nxt[17] ? MAXV : b_nxt[16:0];           // never above MAXV

  // The two channels must always hold complementary values.
  wire mism = (acc_a != ~acc_b);

  // ------------------------------------------------------------------
  // Windowed watchdog on the samples actually taken by both channels.
  // Field mode: the period must be 0.75 s .. 1.5 s, which keeps the trip
  // times inside the UL 2034 windows. Demo mode: only the 1.5 s time-out
  // applies (samples come from the internal 64 Hz tick).
  // ------------------------------------------------------------------
  reg [7:0] wcnt;
  reg       wd_armed, wd_fault;
  always @(posedge clk or negedge rstn)
    if (!rstn) begin
      wcnt     <= 8'd0;
      wd_armed <= 1'b0;
      wd_fault <= 1'b0;
    end else if (in_bist) begin
      wcnt <= 8'd0;
    end else if (kick) begin
      if (~demo_q & wd_armed & (wcnt < 8'd48)) wd_fault <= 1'b1;  // faster than 0.75 s
      wd_armed <= 1'b1;
      wcnt     <= 8'd0;
    end else if (t64) begin
      if (wcnt >= 8'd96) wd_fault <= 1'b1;                        // no sample for 1.5 s
      else               wcnt <= wcnt + 8'd1;
    end

  // ------------------------------------------------------------------
  // Clean-air check: 64 consecutive samples below the baseline are needed
  // before the valve can be permitted. It runs again after every reset,
  // so cycling the power cannot be used to relight while CO is present.
  // ------------------------------------------------------------------
  reg  [6:0] clean;
  wire       air_ok = clean[6];
  always @(posedge clk or negedge rstn)
    if (!rstn)                 clean <= 7'd0;
    else if (in_bist)          clean <= 7'd0;
    else if (upd_qa) begin
      if (a_hi)                clean <= 7'd0;
      else if (!air_ok)        clean <= clean + 7'd1;
    end

  // ------------------------------------------------------------------
  // Supervisor
  // ------------------------------------------------------------------
  wire dose_low   = ~warn_a & ~warn_b;
  wire hard_fault = ~onehot_ok | wd_fault | (mism & ~st[S_BC]);

  assign clr = st[S_BC] & (bcnt == 9'd3) & mism;

  always @(posedge clk or negedge rstn)
    if (!rstn) begin
      st     <= 6'b000001;
      bcnt   <= 9'd0;
      demo_q <= 1'b0;
    end else if (hard_fault) begin
      st <= 6'b100000;
    end else begin
      case (1'b1)
        st[S_BT]: begin
          bcnt <= bcnt + 9'd1;
          if (bcnt == 9'd400) begin
            bcnt   <= 9'd0;
            demo_q <= dem_s[1];
            st     <= (trip_a & trip_b) ? 6'b000010 : 6'b100000;
          end
        end
        st[S_BC]: begin
          bcnt <= bcnt + 9'd1;
          if (bcnt == 9'd3) st <= mism ? 6'b000100 : 6'b100000;
        end
        st[S_STBY]: begin
          if (trip_a | trip_b) st <= 6'b010000;
          else if (arm_ok & chain_ok & dose_low & wd_armed & air_ok) st <= 6'b001000;
        end
        st[S_RUN]: begin
          if (trip_a | trip_b) st <= 6'b010000;
          else if (!chain_ok)  st <= 6'b000100;
        end
        st[S_TRIP]: begin
          if (arm_ok & dose_low) st <= 6'b000100;
        end
        default: ;
      endcase
    end

  // ------------------------------------------------------------------
  // Dynamic valve permission: a two-flop ring that advances only while
  // each channel, on its own evidence, agrees that running is safe.
  // An external AC-coupled driver keeps the valve open only while
  // valve_a / valve_b keep toggling (about 1 kHz, in quadrature).
  // ------------------------------------------------------------------
  reg  ph_a, ph_b;
  wire run_ok = st[S_RUN] & onehot_ok & chain_ok & ~mism;
  wire ok_a   = run_ok & ~trip_a;
  wire ok_b   = run_ok & ~trip_b;

  always @(posedge clk or negedge rstn)
    if (!rstn) begin
      ph_a <= 1'b0;
      ph_b <= 1'b0;
    end else if (!st[S_RUN]) begin
      ph_a <= 1'b0;
      ph_b <= 1'b0;
    end else if (t_ring) begin
      if (ok_a) ph_a <= ~ph_b;
      if (ok_b) ph_b <= ph_a;
    end

  // ------------------------------------------------------------------
  // Display and outputs
  // ------------------------------------------------------------------
  reg [6:0] seg;
  always @(*) begin
    if (st[S_FAULT] | ~onehot_ok) seg = 7'h71;  // F
    else if (in_bist)             seg = 7'h7F;  // 8 (lamp test)
    else if (st[S_TRIP])          seg = dose_low ? 7'h58 : 7'h39;  // c: may be re-armed, C: locked
    else if (st[S_STBY] & ~air_ok) seg = 7'h73; // P (waiting for clean air)
    else
      case (acc_a[16:13])                       // dose in steps of 8192 (about TH/10)
        4'd0:    seg = 7'h3F;
        4'd1:    seg = 7'h06;
        4'd2:    seg = 7'h5B;
        4'd3:    seg = 7'h4F;
        4'd4:    seg = 7'h66;
        4'd5:    seg = 7'h6D;
        4'd6:    seg = 7'h7D;
        4'd7:    seg = 7'h07;
        4'd8:    seg = 7'h7F;
        4'd9:    seg = 7'h6F;
        default: seg = 7'h77;                   // A
      endcase
  end

  wire alarm = st[S_TRIP] & pre[12];            // 4 Hz beeping
  wire fault = st[S_FAULT] | ~onehot_ok;

  assign uo_out  = {st[S_RUN] & onehot_ok, seg};
  assign uio_out = {fault, alarm, ph_b, ph_a, 4'b0000};
  assign uio_oe  = 8'b1111_0000;

  // List all unused inputs to prevent warnings
  wire _unused = &{ena, uio_in[7:4], 1'b0};

endmodule
