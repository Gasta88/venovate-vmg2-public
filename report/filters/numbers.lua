-- numbers.lua — pandoc filter substituting {{pX.layer.quantile}} and
-- {{platform.layer.quantile}} placeholders (grammar widened to
-- (p[1-9]|platform) by issue-57 WI-16) from report/generated/numbers.yaml
-- (issue-53 D-11; display contract superseded for tables by issue-59 WI-20).
--
-- Rendering (issue-59 WI-20): every monetary placeholder — prose AND table
-- cells alike — renders through money_display: € with a K/M/B magnitude
-- suffix and exactly one decimal (values below €1,000 as a plain integer €).
-- The raw-integer table rendering of Appendix B rule 4 is retired; one
-- formatter serves both contexts. {{pX.som.share_of_tam}} renders its
-- one-decimal string in table cells. Placeholders must contain no
-- spaces (they then arrive as single Str nodes; the gsub also handles
-- placeholders embedded in a larger Str).
--
-- Loud failure (repo convention): an unresolved or malformed placeholder, or
-- a missing/unparsable numbers.yaml, raises a Lua error -> pandoc exits
-- non-zero, never leaks a literal {{...}} into the deliverable.
--
-- numbers.yaml path resolution: script-relative (report/filters/ ->
-- report/generated/numbers.yaml), falling back to the working-directory path
-- report/generated/numbers.yaml.

local function script_dir()
  local src = debug.getinfo(1, "S").source
  if src:sub(1, 1) == "@" then src = src:sub(2) end
  return (src:match("^(.*)[/\\][^/\\]*$")) or "."
end

local function load_numbers()
  local candidates = {
    script_dir() .. "/../generated/numbers.yaml",
    "report/generated/numbers.yaml",
  }
  local path, f
  for _, c in ipairs(candidates) do
    f = io.open(c, "r")
    if f then path = c break end
  end
  if not f then
    error("numbers.lua: numbers.yaml not found (tried: " ..
          table.concat(candidates, ", ") .. ")")
  end
  local numbers = {}
  local cur
  for line in f:lines() do
    if not line:match("^#") then
      -- Top-level keys: the six package prefixes p1..p9 and the platform
      -- block (issue-57 WI-16 grammar widening `(p[1-9]|platform)`; Lua
      -- patterns have no alternation, so the two shapes are matched in turn).
      local pkg = line:match("^(p[1-9]):%s*$") or line:match("^(platform):%s*$")
      if pkg then
        cur = pkg
        numbers[pkg] = {}
      else
        local layer, body = line:match("^  (%a+): {(.+)}%s*$")
        if layer and cur then
          local t = {}
          -- Value atoms: integers, or the one-decimal share_of_tam float
          -- (issue-59 WI-22 grammar v2; tonumber keeps int/float subtypes).
          -- Strict segment scan (issue-59 review): each comma-separated
          -- segment must be exactly `key: value`; any residue — e.g. the
          -- malformed `share_of_tam: 0,1` the Python mirror rejects — raises
          -- loudly instead of being silently truncated.
          local pos = 1
          while pos <= #body do
            local k, v, nxt = body:match(
              "^%s*(%a[%w_]*)%s*:%s*(-?%d+%.?%d*)%s*()", pos)
            if not k or not (v:match("^-?%d+$") or v:match("^-?%d+%.%d+$")) then
              error("numbers.lua: malformed flow-map segment in layer '" ..
                    layer .. "': " .. body:sub(pos))
            end
            t[k] = tonumber(v)
            pos = nxt
            if pos <= #body then
              if body:sub(pos, pos) ~= "," then
                error("numbers.lua: malformed flow-map segment in layer '" ..
                      layer .. "': " .. body:sub(pos))
              end
              pos = pos + 1
            end
          end
          numbers[cur][layer] = t
        end
      end
    end
  end
  f:close()
  if next(numbers) == nil then
    error("numbers.lua: numbers.yaml empty or unparsable: " .. path)
  end
  return numbers
end

local NUMBERS = load_numbers()

-- € with a K/M/B magnitude suffix and exactly one decimal at that magnitude
-- (issue-59 WI-20, replacing money_2sf). Values below €1,000 render as a
-- plain integer €; 0 renders "€0"; the sign is preserved. Never bare
-- tostring for the mantissa — explicit %.1f (V-1: Lua 5.4 int/float subtypes).
local function money_display(n)
  if n == 0 then return "€0" end
  local sign = n < 0 and "-" or ""
  local a = math.abs(n)
  local suffix, mag = "", 1
  if a >= 1e9 then suffix, mag = "B", 1e9
  elseif a >= 1e6 then suffix, mag = "M", 1e6
  elseif a >= 1e3 then suffix, mag = "K", 1e3 end
  if mag == 1 then return sign .. "€" .. string.format("%.0f", a) end
  -- K/M boundary guard (issue-59 review): a mantissa that rounds to 1000.0
  -- at the current magnitude must promote to the next magnitude, so values
  -- in [999,950, 999,999] render "€1.0M", never "€1000.0K". B is the top
  -- suffix in the display contract, so it is not promoted.
  if mag < 1e9 and string.format("%.1f", a / mag) == "1000.0" then
    if mag == 1e3 then suffix, mag = "M", 1e6
    else suffix, mag = "B", 1e9 end
  end
  return sign .. "€" .. string.format("%.1f", a / mag) .. suffix
end

local function render(pkg, layer, q, exact)
  local l = NUMBERS[pkg]
  if not l then
    error("numbers.lua: unresolved placeholder {{" .. pkg .. "." .. layer ..
          "." .. q .. "}} — package key '" .. pkg .. "' not in numbers.yaml")
  end
  local t = l[layer]
  if not t then
    error("numbers.lua: unresolved placeholder {{" .. pkg .. "." .. layer ..
          "." .. q .. "}} — layer '" .. layer .. "' absent for " .. pkg ..
          " (document it in the evidence map, never invent it)")
  end
  local v = t[q]
  if v == nil then
    error("numbers.lua: unresolved placeholder {{" .. pkg .. "." .. layer ..
          "." .. q .. "}} — quantile '" .. q .. "' not in numbers.yaml")
  end
  -- Monetary keys render money_display in prose AND tables (issue-59 WI-20:
  -- the exact path no longer emits raw integers). The derived
  -- share_of_tam renders its one-decimal string in table cells only.
  if exact and q == "share_of_tam" then
    return string.format("%.1f", v)
  end
  return money_display(v)
end

-- Monetary placeholders render money_display in both prose and tables
-- (issue-59 WI-20); the exact flag now only distinguishes the table-cell
-- rendering of the derived share_of_tam (one-decimal string) from prose.
-- Ordering note: pandoc visits children before parents, so a global Str
-- handler would substitute table placeholders in prose style before any
-- Table handler runs. The fix is an explicit filter list: the Table filter
-- runs FIRST (walking the table's Strs with the exact flag set), the plain
-- Str filter second (already-substituted table cells contain no "{{" and
-- are skipped).
local IN_TABLE = false

local function subst(text)
  -- Grammar (p[1-9]|platform).(%a+).(%a[%w_]*) — Lua patterns have no
  -- alternation, so the two top-level shapes run as two gsubs in order
  -- (issue-57 WI-16 grammar widening, mirrored in check_numbers.py and
  -- make_numbers.py). Guard: {{pX.som.share_of_tam}} is a derived
  -- one-decimal percentage and is valid ONLY in table cells (exact render);
  -- in prose the money renderer below would format it as € — the report
  -- prose keeps the qualitative statement instead (spec D-23 constraint).
  local function render_key(pkg, layer, q)
    return render(pkg, layer, q, IN_TABLE)
  end
  local out = text:gsub("{{(p[1-9])%.(%a+)%.(%a[%w_]*)}}", render_key)
  out = out:gsub("{{(platform)%.(%a+)%.(%a[%w_]*)}}", render_key)
  if out:find("{{") then
    error("numbers.lua: malformed/unresolved placeholder in: " .. text)
  end
  if out ~= text then return pandoc.Str(out) end
  return nil
end

local function str_filter(el)
  if not el.text:find("{{") then return nil end
  return subst(el.text)
end

local function table_filter(tbl)
  IN_TABLE = true
  local walked = pandoc.walk_block(tbl, {Str = str_filter})
  IN_TABLE = false
  return walked
end

return {
  {Table = table_filter},
  {Str = str_filter},
}
