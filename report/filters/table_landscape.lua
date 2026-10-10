-- Rotate wide tables onto landscape pages.
--
-- Pandoc divides \linewidth evenly across every column of a pipe table, so a
-- 10-column table in a portrait text block gets roughly half an inch per cell.
-- Long tokens then have nowhere to go and print over the neighbouring column.
-- Turning the page sideways widens the text block enough to hold those tables
-- at a readable font size instead of shrinking them to fit.
--
-- Only wide tables are rotated; narrow ones stay in the normal page flow.

local LANDSCAPE_MIN = 6

-- Both settings are scoped: `landscape` is an environment, hence a group, so
-- they apply to this table only and revert afterwards. \tablefontsize is read
-- by the longtable redefinition in header.tex.
local BEGIN = table.concat({
  '\\begin{landscape}',
  '\\renewcommand{\\tablefontsize}{\\footnotesize}',
  '\\setlength{\\tabcolsep}{4pt}',
}, '\n')

local END = '\\end{landscape}'

function Table(el)
  if #el.colspecs >= LANDSCAPE_MIN then
    return {
      pandoc.RawBlock('latex', BEGIN),
      el,
      pandoc.RawBlock('latex', END),
    }
  end
end
