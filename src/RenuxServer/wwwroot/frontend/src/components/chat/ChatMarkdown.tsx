import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeExternalLinks from 'rehype-external-links'
import remarkGfm from 'remark-gfm'

type ChatMarkdownProps = {
  content: string
  onCitationClick?: (citationNumber: number) => void
}

const CITATION_LINK_PREFIX = 'dongttok-citation:'

const toCitationMarkdown = (content: string) =>
  content.replace(/\[(?:문서)?(\d{1,2})\](?!\()/g, (_, citationNumber: string) => {
    const normalized = Number(citationNumber)
    if (!Number.isInteger(normalized) || normalized < 1) return `[${citationNumber}]`
    return `[문서${normalized}](${CITATION_LINK_PREFIX}${normalized})`
  })

const ChatMarkdown = ({ content, onCitationClick }: ChatMarkdownProps) => {
  const markdownContent = useMemo(() => toCitationMarkdown(content), [content])

  return (
    <ReactMarkdown
      className="chat-bubble__text"
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[[rehypeExternalLinks, { target: '_blank', rel: ['noopener', 'noreferrer'] }]]}
      components={{
        a: ({ href, children, ...props }) => {
          if (href?.startsWith(CITATION_LINK_PREFIX)) {
            const citationNumber = Number(href.slice(CITATION_LINK_PREFIX.length))
            return (
              <button
                type="button"
                className="chat-citation-link"
                onClick={(event) => {
                  event.stopPropagation()
                  if (Number.isInteger(citationNumber)) {
                    onCitationClick?.(citationNumber)
                  }
                }}
              >
                {children}
              </button>
            )
          }

          return (
            <a
              {...props}
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: '#0d6efd', textDecoration: 'underline', pointerEvents: 'auto', cursor: 'pointer' }}
              onClick={(event) => event.stopPropagation()}
            >
              {children}
            </a>
          )
        },
      }}
    >
      {markdownContent}
    </ReactMarkdown>
  )
}

export default ChatMarkdown
