export interface FreeWriteCorrection {
  original: string
  corrected: string
  explanation: string
}

export type AnswerSegment =
  | { type: 'plain'; text: string }
  | { type: 'fix'; original: string; corrected: string }

interface Occurrence {
  start: number
  end: number
  wholeWord: boolean
  priority: number
}

interface Candidate extends Occurrence {
  correction: number
}

const WORD_CHAR = /[\p{L}\p{N}]/u

function isWordChar(char: string | undefined): boolean {
  return char !== undefined && WORD_CHAR.test(char)
}

// A match is "whole word" when the fragment does not continue a word on
// either side of the answer. Sides where the fragment itself starts/ends with
// punctuation or whitespace are not constrained.
function isWholeWord(
  answer: string,
  needle: string,
  start: number,
  end: number
): boolean {
  const startsWithWord = isWordChar(needle[0])
  const endsWithWord = isWordChar(needle[needle.length - 1])
  return (
    (!startsWithWord || !isWordChar(answer[start - 1])) &&
    (!endsWithWord || !isWordChar(answer[end]))
  )
}

// Keep all variants available, deduplicating ranges with exact/as-is matches
// preferred. Search the original answer so case folding cannot shift offsets.
function findOccurrences(answer: string, original: string): Occurrence[] {
  const occurrences = new Map<string, Occurrence>()
  const variants = [original, original.trim()].filter(
    (candidate, index, all) => candidate && all.indexOf(candidate) === index
  )
  for (const [variantIndex, candidate] of variants.entries()) {
    const escaped = candidate.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    for (const [caseIndex, flags] of ['gu', 'giu'].entries()) {
      // Lookahead retains overlapping occurrences for the allocation pass.
      const pattern = new RegExp(`(?=(${escaped}))`, flags)
      for (const match of answer.matchAll(pattern)) {
        const start = match.index
        const end = start + match[1].length
        const key = `${start}:${end}`
        if (!occurrences.has(key)) {
          occurrences.set(key, {
            start,
            end,
            wholeWord: isWholeWord(answer, match[1], start, end),
            priority: variantIndex * 2 + caseIndex,
          })
        }
      }
    }
  }
  return [...occurrences.values()]
}

// Locates each correction's `original` fragment in the submitted answer and
// splits the answer into plain/fix segments. Each correction is assigned one
// non-overlapping occurrence: repeated identical fragments consume successive
// occurrences. Whole-word matches beat subwords, then exact/as-is matches beat
// fallbacks; ties follow answer order, preferring longer same-start fragments.
// Corrections whose fragment cannot be placed yield no segment — they are
// still shown in the corrections list below the answer.
export function annotateAnswer(
  answer: string,
  corrections: FreeWriteCorrection[]
): AnswerSegment[] {
  const candidates: Candidate[] = corrections.flatMap((correction, index) =>
    correction.original && correction.corrected
      ? findOccurrences(answer, correction.original).map((occurrence) => ({
          ...occurrence,
          correction: index,
        }))
      : []
  )
  candidates.sort(
    (a, b) =>
      a.priority - b.priority ||
      a.start - b.start ||
      b.end - b.start - (a.end - a.start) ||
      a.correction - b.correction
  )

  const matches: Array<{ start: number; end: number; corrected: string }> = []
  const placed = new Set<number>()
  const overlaps = (start: number, end: number) =>
    matches.some((match) => start < match.end && end > match.start)
  for (const wholeWordOnly of [true, false]) {
    for (const candidate of candidates) {
      if (placed.has(candidate.correction)) continue
      if (wholeWordOnly && !candidate.wholeWord) continue
      if (overlaps(candidate.start, candidate.end)) continue
      placed.add(candidate.correction)
      matches.push({
        start: candidate.start,
        end: candidate.end,
        corrected: corrections[candidate.correction].corrected,
      })
    }
  }
  matches.sort((a, b) => a.start - b.start)

  const segments: AnswerSegment[] = []
  let cursor = 0
  for (const match of matches) {
    if (match.start > cursor) {
      segments.push({ type: 'plain', text: answer.slice(cursor, match.start) })
    }
    segments.push({
      type: 'fix',
      original: answer.slice(match.start, match.end),
      corrected: match.corrected,
    })
    cursor = match.end
  }
  if (cursor < answer.length) {
    segments.push({ type: 'plain', text: answer.slice(cursor) })
  }
  return segments
}
