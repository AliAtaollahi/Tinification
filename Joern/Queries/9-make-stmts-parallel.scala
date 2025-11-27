import io.shiftleft.semanticcpg.language._
import io.shiftleft.codepropertygraph.generated.nodes.{Method, Block, ControlStructure, AstNode}

object CfgByClass {

  // ---------- helpers ----------
  def ownerTypeOf(fullName: String): String =
    fullName.takeWhile(_ != '.')

  def simpleTypeName(tf: String): String =
    Option(tf).getOrElse("").split("[.$]").lastOption.getOrElse(tf)

  // Build "Owner.method(p1, p2, ...)" (skip implicit 'this', prefer names, fallback to types)
  def prettyMethodLabel(m: Method): String = {
    val owner = m.typeDecl.name.headOption.getOrElse(ownerTypeOf(m.fullName))
    val params =
      m.parameter.orderGt(0).l.map { p =>
        val nm  = Option(p.name).map(_.trim).filter(n => n.nonEmpty && n != "this")
        val tpe = simpleTypeName(Option(p.typeFullName).getOrElse(""))
        nm.getOrElse(tpe)
      }.mkString(", ")
    owner + "." + m.name + "(" + params + ")"
  }

  // HTML-escape for Graphviz HTML-like labels
  def html(s: String): String =
    s.replace("&", "&amp;")
      .replace("<", "&lt;")
      .replace(">", "&gt;")
      .replace("\"", "&quot;")

  // Graphviz id helpers (stable + unique)
  def sanitize(s: String): String = s.replaceAll("[^A-Za-z0-9_]", "_")
  private def h(s: String): String = Integer.toHexString(s.hashCode).take(6)

  def stateVarId(owner: String, field: String): String =
    sanitize(owner) + "__sv_" + sanitize(field) + "_" + h(field)

  def paramId(owner: String, mFull: String, pName: String, idx: Int): String =
    sanitize(owner) + "__" + sanitize(mFull) + "__param_" + idx + "_" + sanitize(pName) + "_" + h(pName)

  def ctrlNodeId(owner: String, mFull: String, kind: String, line: Option[Int]): String = {
    val ln = line.map(_.toString).getOrElse("u")
    sanitize(owner) + "__" + sanitize(mFull) + "__ctrl_" + sanitize(kind.toLowerCase) + "_" + ln
  }

  // Prefix numeric ids inside a DOT snippet (ids inside Joern's CFG are just numbers)
  def prefixIds(dot: String, prefix: String): String = {
    val idRx = "\"(\\d+)\"".r
    idRx.replaceAllIn(dot, m => "\"" + prefix + "_" + m.group(1) + "\"")
  }

  // Strip Joern's outer digraph wrapper
  def stripDotWrapper(dot: String): String =
    dot.linesIterator
      .filterNot(_.trim.startsWith("digraph"))
      .filterNot(_.trim.startsWith("node "))
      .filterNot(_.trim == "}")
      .mkString("\n")

  // ---------- abstraction with root relabel ----------

  case class Abstracted(
    inner: String,
    lineToKeptNodeId: Map[String,String],  // raw ids (unprefixed) ONLY for kept (non-suppressed) lines
    rootIds: Set[String],                  // raw root ids (unprefixed)
    lineToCode: Map[String,String]         // code for ALL lines (kept + suppressed), after patching
  )

  /**
    * We take a raw per-method `.dotCfg` from Joern and:
    * - keep at most one node per *source line*
    * - skip control-structure condition lines (if/else-if headers)
    * - aggressively repair garbage nodes like UNKNOWN/???;:
    *     * prefer a real sibling CFG node
    *     * else pull code from AST (same line or ±2 lines)
    *     * when repaired via AST, force kind="CALL" so label shows CALL not UNKNOWN
    * - relabel the single method root node with prettyMethodLabel(...)
    *
    * The output "inner" is a DOT snippet without outer digraph {}.
    */
  def abstractDotByLine(
      dot: String,
      rootLabelOverride: Option[String] = None,
      suppressLines: Set[String] = Set.empty,
      astHints: Map[String,String] = Map.empty
  ): Abstracted = {
    import scala.util.matching.Regex
    import scala.util.Try

    val nodeRx: Regex =
      "\"(\\d+)\"\\s*\\[label\\s*=\\s*<([^,>]+),\\s*(\\d+)<BR/>(.*?)>\\s*\\]".r

    case class Node(
      id: String,
      kind: String,
      line: String,
      code: String,
      methodName: Option[String] = None
    )

    def asLong(s: String): Long = Try(s.toLong).getOrElse(Long.MaxValue)

    // Parse CFG nodes from Joern's dotCfg
    val nodesAll: List[Node] =
      nodeRx.findAllMatchIn(dot).map { m =>
        val kind = m.group(2)
        val code = m.group(4)
        val methName =
          if (kind.startsWith("METHOD") || kind.equalsIgnoreCase("init")) Some(code) else None
        Node(
          id         = m.group(1),
          kind       = kind,
          line       = m.group(3),
          code       = code,
          methodName = methName
        )
      }.toList

    if (nodesAll.isEmpty)
      return Abstracted("", Map.empty, Set.empty, Map.empty)

    // Pick one "root" node (METHOD or <init>) deterministically
    val methodRootIds: List[String] =
      nodesAll
        .filter(n => n.kind.startsWith("METHOD") || n.kind.equalsIgnoreCase("init"))
        .map(_.id)
        .sortBy(asLong)

    val chosenRootIdOpt: Option[String] = methodRootIds.headOption
    val rootIds: Set[String] = chosenRootIdOpt.toSet

    // ---- helpers to decide if a node is garbage and to try repairing it ----

    def isGarbageKind(kind: String): Boolean =
      kind.equalsIgnoreCase("UNKNOWN")

    def isGarbageCode(code: String): Boolean = {
      val t = code.trim
      t.isEmpty || t.startsWith("???")
    }

    // bad node means Joern didn't recover meaningful code (typical for post-merge nodes after if/else)
    def looksBad(n: Node): Boolean =
      isGarbageKind(n.kind) || isGarbageCode(n.code)

    // ranking: prefer METHOD/init > good stuff > junk
    def nodePriority(n: Node): Int = {
      val isMethodRoot = n.kind.startsWith("METHOD") || n.kind.equalsIgnoreCase("init")
      if (isMethodRoot) 3
      else if (!looksBad(n)) 2
      else 1
    }

    // build numeric astHints so we can fallback to nearby lines (CFG line numbers can drift)
    val astHintsInt: Map[Int,String] =
      astHints.flatMap { case (k,v) => scala.util.Try(k.toInt).toOption.map(_ -> v) }

    def bestHintFor(lineStr: String): Option[String] = {
      val lnOpt = scala.util.Try(lineStr.toInt).toOption
      lnOpt.flatMap { ln0 =>
        astHintsInt.toList
          .sortBy { case (ln, _) => math.abs(ln - ln0) }
          .collectFirst {
            case (ln, code)
                if math.abs(ln - ln0) <= 2 && {
                  val t = code.trim
                  t.nonEmpty && !t.startsWith("???")
                } =>
              code
          }
      }
    }

    /**
      * pickBestNodeForLine:
      *   choose a representative node for a given source line.
      *   if it's garbage (UNKNOWN, ???;), try:
      *     - sibling CFG node on same line that's non-garbage, preferring CALL
      *     - AST code from same/nearby line; force kind="CALL" so output label is sane
      */
    def pickBestNodeForLine(nodesOnThatLine: List[Node]): Node = {
      // Start with best by priority / code length
      val best0 =
        nodesOnThatLine.maxBy(n => (nodePriority(n), n.code.length))

      if (!looksBad(best0)) {
        best0
      } else {
        // Try to repair "UNKNOWN, ???;" via siblings
        val nonGarbage = nodesOnThatLine.filterNot(looksBad)

        // Prefer CALL node first
        val callNodeOpt =
          nonGarbage.find(n => n.kind.equalsIgnoreCase("CALL"))

        val fallbackGoodOpt =
          callNodeOpt.orElse(
            nonGarbage.sortBy(-_.code.length).headOption
          )

        fallbackGoodOpt match {
          case Some(good) =>
            // Keep original id/line (edges elsewhere refer to this id!)
            // but show "good" node's kind/code in the label.
            best0.copy(
              kind        = good.kind,
              code        = good.code,
              methodName  = good.methodName
            )

          case None =>
            // No sibling saved us. Pull from AST.
            bestHintFor(best0.line) match {
              case Some(hintCode) =>
                // Force kind to CALL so we don't render "UNKNOWN"
                best0.copy(
                  kind = if (best0.kind.equalsIgnoreCase("UNKNOWN")) "CALL" else best0.kind,
                  code = hintCode
                )
              case None =>
                // Nothing we can do. We'll leave UNKNOWN ???; as a last resort.
                best0
            }
        }
      }
    }

    // choose best node for every source line in this method
    val bestAllByLine: Map[String, Node] = {
      val byLine: Map[String, List[Node]] =
        nodesAll.groupBy(_.line).view.mapValues(_.toList).toMap
      byLine.map { case (line, ns) =>
        line -> pickBestNodeForLine(ns)
      }
    }

    // filter out lines we explicitly "suppress" (e.g. the if(...) test lines),
    // but keep *everything else*, including lines that were garbage and then repaired.
    val keptByLine: Map[String, Node] =
      bestAllByLine.filterNot { case (ln, _) => suppressLines.contains(ln) }

    val keptNodes: List[Node] = keptByLine.values.toList

    // turn each kept node into a Graphviz node line
    val nodeLines: List[String] =
      keptNodes
        .sortBy(n => scala.util.Try(n.line.toInt).getOrElse(Int.MaxValue))
        .map { n =>
          val label =
            n.kind match {
              case k if k.startsWith("METHOD") && n.methodName.isDefined =>
                n.methodName.get
              case k if k.equalsIgnoreCase("init") && n.methodName.isDefined =>
                n.methodName.get
              case _ =>
                // final label text inside the box
                n.kind + ", " + n.line + "<BR/>" + n.code
            }
          "\"" + n.id + "\" [label = <" + label + "> ]"
        }

    // no internal edges for the abstracted subgraph; deps are stitched later
    val edgeLines: List[String] = Nil

    // relabel the chosen root node with the pretty owner.method(sig)
    val rootLines: List[String] =
      chosenRootIdOpt.toList.map { id =>
        val text = html(rootLabelOverride.getOrElse("METHOD"))
        "\"" + id + "\" [label = <<FONT>" + text + "</FONT>> ]"
      }

    val inner = (nodeLines ++ edgeLines ++ rootLines).mkString("\n")

    // map each line -> node id (after filtering), for later edge stitching
    val keptMap: Map[String,String] =
      keptByLine.map{ case (ln, n) => ln -> n.id }

    // map each line -> final code string we decided on
    val codeMap: Map[String,String] =
      bestAllByLine.map{ case (ln, n) => ln -> n.code }

    Abstracted(
      inner           = inner,
      lineToKeptNodeId= keptMap,
      rootIds         = rootIds,
      lineToCode      = codeMap
    )
  }

  // ---------- main pipeline ----------
  def run(fileRegex: String = ".*\\.java"): String = {
    val methods =
      cpg.method
        .where(_.isExternal(false))
        .where(_.file.name(fileRegex))
        .l

    // group methods by owning type so we can make subgraph clusters per class
    val methodsByOwner =
      methods.groupBy { m => m.typeDecl.name.headOption.getOrElse(ownerTypeOf(m.fullName)) }

    // Inner/user class basenames so we don't confuse them with fields
    val innerClassBaseNames: Set[String] =
      cpg.typeDecl
        .where(_.isExternal(false))
        .name
        .l
        .map { n =>
          val base = n.split("\\$").lastOption.getOrElse(n)
          base.toLowerCase
        }
        .toSet

    // caches / accumulators
    val methodToPrefixedRootId = scala.collection.mutable.Map.empty[String, String]
    val methodToLineMap        = scala.collection.mutable.Map.empty[String, Map[String,String]]
    val methodToLineCode       = scala.collection.mutable.Map.empty[String, Map[String,String]]
    val methodToBody           = scala.collection.mutable.Map.empty[String, String]
    val ownerToBodies          = scala.collection.mutable.Map.empty[String, scala.collection.mutable.ListBuffer[String]]

    val methodParamIdxToNodeId = scala.collection.mutable.Map.empty[String, Map[Int, String]]
    val ownerStateVarToNodeId  = scala.collection.mutable.Map.empty[(String,String), String]
    val ownerToFieldNames      = scala.collection.mutable.Map.empty[String, Set[String]]

    val emittedStateNodeIds    = scala.collection.mutable.Set.empty[String]
    val emittedParamNodeIds    = scala.collection.mutable.Set.empty[String]
    val emittedCtrlNodeIds     = scala.collection.mutable.Set.empty[String]  // synthetic IF/ELSE/etc nodes

    val blueEdges        = scala.collection.mutable.Set.empty[String] // node -> stateVar (write) / node -> param (write)
    val redEdges         = scala.collection.mutable.Set.empty[String] // stateVar -> node (read) / param -> node (read)
    val actDepEdges      = scala.collection.mutable.Set.empty[String] // callsite -> callee root ("act dep")
    val purpleArgEdges   = scala.collection.mutable.Set.empty[String] // callsite -> callee params
    val ctrlScopeEdges   = scala.collection.mutable.Set.empty[String] // ctrl dep edges

    // We'll also collect which condition nodes mention which fields,
    // so we add state-var "used by condition" edges later.
    val condUse = scala.collection.mutable.ListBuffer.empty[(String, String, String)]
    // (owner, ctrlNodeId, condText)

    // regexes
    val thisFieldRx        = "\\bthis\\.([A-Za-z_]\\w*)".r

    // assignment operators (longer first; '=' guarded to avoid '==')
    val assignOpsPattern = "(?:<<=|>>=|\\+=|-=|\\*=|/=|%=|\\|=|&=|\\^=|=(?!=))"

    // explicit this.field on LHS of assignment
    val assignLhsThisRx    = (s"\\bthis\\.([A-Za-z_]\\w*)\\s*$assignOpsPattern").r

    // ---------- walk all internal methods ----------
    methods.foreach { m =>
      val owner = m.typeDecl.name.headOption.getOrElse(ownerTypeOf(m.fullName))
      val pfx   = (owner + "__" + m.fullName).replaceAll("[^A-Za-z0-9_]", "_")
      val raw   = m.dotCfg.l.headOption.getOrElse("")

      val bufForOwner = ownerToBodies.getOrElseUpdate(owner, scala.collection.mutable.ListBuffer.empty[String])

      // record declared fields for this owner only once
      val ownerFields: Set[String] = ownerToFieldNames.getOrElseUpdate(
        owner,
        m.typeDecl.member.name.l.toSet
      )

      // --- parameter node "stubs" for this method ---
      val params = m.parameter.orderGt(0).l
      val paramIdxToId: Map[Int,String] =
        params.zipWithIndex.map { case (p, i0) =>
          val idx  = i0 + 1
          val name = Option(p.name).getOrElse(s"param$idx")
          val pid  = paramId(owner, m.fullName, name, idx)
          if (!emittedParamNodeIds.contains(pid)) {
            val label = sanitize(m.name) + "_param_" + sanitize(name)
            bufForOwner += "\"" + pid + "\" [label = <<FONT>" + label + "</FONT>> ]"
            emittedParamNodeIds += pid
          }
          idx -> pid
        }.toMap
      methodParamIdxToNodeId += (m.fullName -> paramIdxToId)

      if (raw.nonEmpty) {

        // AST hints: map "line" -> "best code" from AST nodes in that method
        val astHints: Map[String,String] = {
          val pairs =
            m.ast.collectAll[AstNode].l.flatMap { n =>
              val lineOpt = n.lineNumber
              val codeStr = Option(n.code).getOrElse("")
              lineOpt.flatMap { ln =>
                val trimmed = codeStr.trim
                if (trimmed.nonEmpty && !trimmed.startsWith("???"))
                  Some(ln.toString -> trimmed)
                else None
              }
            }

          pairs
            .groupBy(_._1)
            .map { case (ln, xs) =>
              val best = xs.map(_._2).maxBy(_.length)
              ln -> best
            }
        }

        // Don't show condition-expression lines as normal stmt nodes
        val csLines: Set[String] =
          m.controlStructure.lineNumber.l.flatMap(x => Option(x).map(_.toString)).toSet

        val rootLabel = prettyMethodLabel(m)

        // <-- this is the important call to abstractDotByLine with astHints
        val abs = abstractDotByLine(
          dot               = raw,
          rootLabelOverride = Some(rootLabel),
          suppressLines     = csLines,
          astHints          = astHints
        )

        val withWrapper =
          "digraph \"cfg_abstract\" {\nnode [shape=\"rect\"];\n" + abs.inner + "\n}"
        val prefixed  = prefixIds(withWrapper, pfx)
        val stripped  = stripDotWrapper(prefixed)

        val lineMapPrefixed  = abs.lineToKeptNodeId.map{ case (ln,id) => ln -> (pfx + "_" + id) }
        val lineCode         = abs.lineToCode       // patched final code per line
        val rootIdsPrefixed  = abs.rootIds.map(id => pfx + "_" + id)

        methodToLineMap  += (m.fullName -> lineMapPrefixed)
        methodToLineCode += (m.fullName -> lineCode)
        methodToBody     += (m.fullName -> stripped)
        rootIdsPrefixed.headOption.foreach(r => methodToPrefixedRootId += (m.fullName -> r))

        // ---------- STATE VAR NODES ----------
        // we'll inspect all code lines in this method (including suppressed) to see which fields appear
        val allCodes = lineCode.values.mkString("\n")

        val fieldsViaThis = thisFieldRx.findAllMatchIn(allCodes).map(_.group(1)).toSet
        val bareFieldMatches: Set[String] =
          ownerFields.filter { f =>
            val q = java.util.regex.Pattern.quote(f)
            val rx = ("(?<![\\w\\$])" + q + "(?![\\w\\$])").r
            rx.findFirstIn(allCodes).isDefined
          }

        val fieldsUsedFiltered = (fieldsViaThis ++ bareFieldMatches)
          .filterNot(f => innerClassBaseNames.contains(f.toLowerCase))

        fieldsUsedFiltered.foreach { f =>
          val id = ownerStateVarToNodeId.getOrElseUpdate((owner, f), stateVarId(owner, f))
          if (!emittedStateNodeIds.contains(id)) {
            val label = "sv_" + sanitize(f) + " " + html("this." + f)
            bufForOwner += "\"" + id + "\" [label = <<FONT>" + label + "</FONT>> ]"
            emittedStateNodeIds += id
          }
        }

        // ---------- DATA DEP EDGES (redEdges / blueEdges) ----------
        lineMapPrefixed.foreach { case (ln, nodeId) =>
          val code = lineCode.getOrElse(ln, "")

          // state vars on RHS and LHS
          val rhsFieldsThis = thisFieldRx.findAllMatchIn(code).map(_.group(1)).toSet
          val lhsFieldsThis = assignLhsThisRx.findAllMatchIn(code).map(_.group(1)).toSet

          // Also detect bare field refs / bare assignments
          val (lhsBare, anyBare): (Set[String], Set[String]) = {
            val l = scala.collection.mutable.Set.empty[String]
            val u = scala.collection.mutable.Set.empty[String]
            ownerFields.foreach { f =>
              val q = java.util.regex.Pattern.quote(f)
              val lhsRx = ("(?<![\\w\\$])" + q + "\\s*" + assignOpsPattern).r
              val useRx = ("(?<![\\w\\$])" + q + "(?![\\w\\$])").r
              if (lhsRx.findFirstIn(code).isDefined) l += f
              if (useRx.findFirstIn(code).isDefined) u += f
            }
            (l.toSet, u.toSet)
          }

          val lhsSet  = (lhsFieldsThis ++ lhsBare).filterNot(f => innerClassBaseNames.contains(f.toLowerCase))
          val rhsCand = (rhsFieldsThis ++ (anyBare -- lhsBare)).filterNot(f => innerClassBaseNames.contains(f.toLowerCase))

          // Node is influenced by RHS uses (sv -> node)  => style blue later
          val rhsOnly = rhsCand.diff(lhsSet)
          rhsOnly.foreach { f =>
            val sv = ownerStateVarToNodeId.getOrElseUpdate((owner, f), stateVarId(owner, f))
            redEdges += "\"" + sv + "\" -> \"" + nodeId + "\" [color=\"blue\"]"
          }

          // Node writes to LHS vars (node -> sv) => style red later
          lhsSet.foreach { f =>
            val sv = ownerStateVarToNodeId.getOrElseUpdate((owner, f), stateVarId(owner, f))
            blueEdges += "\"" + nodeId + "\" -> \"" + sv + "\" [color=\"red\"]"
          }

          // ---------- PARAM use / def edges ----------
          params.zipWithIndex.foreach { case (p, i0) =>
            val idx   = i0 + 1
            val pName = Option(p.name).getOrElse("")
            if (pName.nonEmpty) {
              val qn    = java.util.regex.Pattern.quote(pName)
              val useRx = ("(?<![\\w\\$])" + qn + "(?![\\w\\$])").r
              val lhsRx = ("(?<![\\w\\$])" + qn + "\\s*" + assignOpsPattern).r

              val isLhs = lhsRx.findFirstIn(code).isDefined
              val used  = useRx.findFirstIn(code).isDefined

              if (used && !isLhs) {
                val pid = paramIdxToId(idx)
                redEdges += "\"" + pid + "\" -> \"" + nodeId + "\" [color=\"blue\"]"
              }
              if (isLhs) {
                val pid = paramIdxToId(idx)
                blueEdges += "\"" + nodeId + "\" -> \"" + pid + "\" [color=\"red\"]"
              }
            }
          }
        }

        // ---------- CONTROL DEPENDENCIES ----------
        try {
          val mBlockOpt: Option[Block] = m.ast.isBlock.headOption
          val rootIdOpt = abs.rootIds.headOption.map(id => pfx + "_" + id)

          (for {
            methodBlock <- mBlockOpt
            rootId      <- rootIdOpt
          } yield (methodBlock, rootId)).foreach { case (methodBlock, rootId) =>

            // map an AST node to the abstracted+prefixed node id via its line
            def idOf(n: AstNode): Option[String] =
              n.lineNumber.map(_.toString).flatMap(lineMapPrefixed.get)

            // earliest line in a list of AST nodes
            def firstLine(nodes: List[AstNode]): Option[Int] =
              nodes.flatMap(_.lineNumber).sorted.headOption

            // create or reuse a synthetic IF/ELSEIF/ELSE/etc. node
            def ensureCtrlNode(labelKind: String, lineOpt: Option[Int], condTextOpt: Option[String]): String = {
              val id = ctrlNodeId(owner, m.fullName, labelKind, lineOpt)
              if (!emittedCtrlNodeIds.contains(id)) {
                val kindUpper = labelKind.toUpperCase
                val lnStr = lineOpt.map(_.toString).getOrElse("?")
                val body = condTextOpt.getOrElse(kindUpper.toLowerCase)
                val label = s"$kindUpper, $lnStr<BR/>" + html(body)
                bufForOwner += "\"" + id + "\" [label = <" + label + "> ]"
                emittedCtrlNodeIds += id
              }
              id
            }

            // add ctrl dep edges parentId -> each stmt in "bodyNodes"
            def attachBranch(parentId: String, bodyNodes: List[AstNode]): Unit = {
              bodyNodes.foreach {
                case b: Block =>
                  b.astChildren.l.foreach {
                    case nestedCs: ControlStructure =>
                      addCtrlFromCS(parentId, nestedCs, isElseIf = false)
                    case n: AstNode =>
                      idOf(n).foreach(oid =>
                        ctrlScopeEdges += s""""$parentId" -> "$oid" [label="ctrl dep"]"""
                      )
                    case _ => ()
                  }
                case nestedCs: ControlStructure =>
                  addCtrlFromCS(parentId, nestedCs, isElseIf = false)
                case n: AstNode =>
                  idOf(n).foreach { oid =>
                    ctrlScopeEdges += s""""$parentId" -> "$oid" [label="ctrl dep"]"""
                  }
              }
            }

            // recursively add IF / ELSEIF / ELSE control structure nodes + edges
            def addCtrlFromCS(parentId: String, cs: ControlStructure, isElseIf: Boolean): Unit = {
              val csLine = cs.lineNumber
              val condText = csLine.flatMap(lnv => lineCode.get(lnv.toString))
              val labelKind = if (isElseIf) "ELSEIF" else Option(cs.controlStructureType).getOrElse("IF").toUpperCase
              val csNodeId = ensureCtrlNode(labelKind, csLine, condText)
              ctrlScopeEdges += s""""$parentId" -> "$csNodeId" [label="ctrl dep"]"""

              // record this condition so we can add sv->cond and param->cond edges
              condUse += ((owner, csNodeId, condText.getOrElse("")))

              // params used in condition => param -> cond (blue-colored edge)
              condText.foreach { cond =>
                params.zipWithIndex.foreach { case (p, i0) =>
                  val idx   = i0 + 1
                  val pName = Option(p.name).getOrElse("")
                  if (pName.nonEmpty) {
                    val q   = java.util.regex.Pattern.quote(pName)
                    val useRx = ("(?<![\\w\\$])" + q + "(?![\\w\\$])").r
                    if (useRx.findFirstIn(cond).isDefined) {
                      val pid = paramIdxToId(idx)
                      redEdges += s""""$pid" -> "$csNodeId" [color="blue"]"""
                    }
                  }
                }
              }

              // Try Joern's whenTrue/whenFalse first
              val (trueNodes, falseNodes) =
                try {
                  val tNodes = cs.whenTrue.astChildren.collectAll[AstNode].l
                  val fNodes = cs.whenFalse.astChildren.collectAll[AstNode].l
                  (tNodes, fNodes)
                } catch {
                  case _: Throwable =>
                    // fallback: guess from AST order
                    val nonCond = cs.astChildren.collectAll[AstNode].whereNot(_.order(1)).l
                    val (tn, fn) =
                      if (nonCond.size >= 2) (List(nonCond.head), nonCond.tail)
                      else (nonCond, Nil)
                    (tn, fn)
                }

              // "then" branch
              attachBranch(csNodeId, trueNodes)

              // "else" branch
              val elseAsElseIf: Option[ControlStructure] =
                falseNodes match {
                  case (c: ControlStructure) :: Nil => Some(c)
                  case b :: Nil if b.isInstanceOf[Block] =>
                    b.asInstanceOf[Block].astChildren.collectAll[ControlStructure].l match {
                      case x :: Nil => Some(x)
                      case _        => None
                    }
                  case _ => None
                }

              elseAsElseIf match {
                case Some(nestedIf) =>
                  // flattened ELSEIF
                  addCtrlFromCS(parentId, nestedIf, isElseIf = true)
                case None if falseNodes.nonEmpty =>
                  // explicit ELSE body
                  val elseLine = firstLine(falseNodes).orElse(csLine)
                  val elseNodeId = ensureCtrlNode("ELSE", elseLine, Some("else"))
                  ctrlScopeEdges += s""""$csNodeId" -> "$elseNodeId" [label="ctrl dep"]"""
                  attachBranch(elseNodeId, falseNodes)
                case _ => () // nothing in else
              }
            }

            // Walk the method body: root -> top-level stmts / control structures
            def addFrom(parentId: String, block: Block): Unit = {
              block.astChildren.l.foreach {
                case cs: ControlStructure =>
                  addCtrlFromCS(parentId, cs, isElseIf = false)
                case child: AstNode       =>
                  idOf(child).foreach { childId =>
                    ctrlScopeEdges += s""""$parentId" -> "$childId" [label="ctrl dep"]"""
                  }
                case _ => ()
              }
            }

            addFrom(rootId, methodBlock)
          }
        } catch {
          case _: Throwable => () // best-effort for ctrl deps; don't blow up the whole graph
        }

        // add this method's "cluster body" content
        bufForOwner += stripped
      }
    }

    // ---------- CONTROL CONDITION → STATE VAR edges ----------
    // we deferred this until after walking everything, so we know all sv nodes exist
    condUse.foreach { case (own, csId, cond) =>
      if (cond.nonEmpty) {
        // which fields from 'own' are read in this condition text?
        val fieldsThis =
          thisFieldRx.findAllMatchIn(cond).map(_.group(1)).toSet
            .filterNot(f => innerClassBaseNames.contains(f.toLowerCase))

        val ownerFields = ownerToFieldNames.getOrElse(own, Set.empty)
        val bareMatches: Set[String] =
          ownerFields.filter { f =>
            val q = java.util.regex.Pattern.quote(f)
            val rx = ("(?<![\\w\\$])" + q + "(?![\\w\\$])").r
            rx.findFirstIn(cond).isDefined
          }

        val fields = fieldsThis ++ bareMatches
        if (fields.nonEmpty) {
          val bufForOwner = ownerToBodies.getOrElseUpdate(own, scala.collection.mutable.ListBuffer.empty[String])
          fields.foreach { f =>
            val sv = ownerStateVarToNodeId.getOrElseUpdate((own, f), stateVarId(own, f))
            if (!emittedStateNodeIds.contains(sv)) {
              val label = "sv_" + sanitize(f) + " " + html("this." + f)
              bufForOwner += s""""$sv" [label = <<FONT>$label</FONT>> ]"""
              emittedStateNodeIds += sv
            }
            // sv used in condition => sv -> ctrlNode
            redEdges += s""""$sv" -> "$csId" [color="blue"]"""
          }
        }
      }
    }

    // ---------- CLUSTER PER CLASS ----------
    val clustersByClass: List[String] =
      methodsByOwner.toList.sortBy(_._1).flatMap { case (owner, _) =>
        val ownerSan = sanitize(owner)
        val bodies = ownerToBodies.get(owner).map(_.toList).getOrElse(Nil)
        if (bodies.isEmpty) None
        else Some(
          "subgraph \"cluster_" + ownerSan + "\" {\n" +
          "  label=\"" + html(owner) + "\";\n" +
          "  " + bodies.mkString("\n") + "\n" +
          "}"
        )
      }

    // ---------- ACTIVITY DEPENDENCY EDGES (callsite -> callee root) ----------
    val internalFullNames: Set[String] = methodToPrefixedRootId.keySet.toSet
    methods.foreach { callerM =>
      val callerMap  = methodToLineMap.getOrElse(callerM.fullName, Map.empty)
      callerM.call.l.foreach { c =>
        val callee  = Option(c.methodFullName).getOrElse("")
        val lineOpt = c.lineNumber.map(_.toString)
        if (lineOpt.isDefined && internalFullNames.contains(callee)) {
          val ln = lineOpt.get
          val srcIdOpt = callerMap.get(ln)
          val dstIdOpt = methodToPrefixedRootId.get(callee)
          if (srcIdOpt.isDefined && dstIdOpt.isDefined && srcIdOpt.get != dstIdOpt.get) {
            actDepEdges += "\"" + srcIdOpt.get + "\" -> \"" + dstIdOpt.get + "\" [label=\"act dep\"]"
          }
        }
      }
    }

    // ---------- ARGUMENT BINDING EDGES (callsite -> callee param nodes) ----------
    methods.foreach { callerM =>
      val callerMap = methodToLineMap.getOrElse(callerM.fullName, Map.empty)
      callerM.call.l.foreach { c =>
        val callee    = Option(c.methodFullName).getOrElse("")
        val lineOpt   = c.lineNumber.map(_.toString)
        val idxMapOpt = methodParamIdxToNodeId.get(callee)
        if (lineOpt.isDefined && idxMapOpt.isDefined) {
          val callNodeOpt = callerMap.get(lineOpt.get)
          if (callNodeOpt.isDefined) {
            idxMapOpt.get.toList.sortBy(_._1).foreach { case (_, pid) =>
              // purple edges from callsite stmt -> each callee param node
              purpleArgEdges += "\"" + callNodeOpt.get + "\" -> \"" + pid + "\" [color=\"purple\"]"
            }
          }
        }
      }
    }

    // ---------- FINAL DOT OUTPUT ----------
    val out =
      "digraph \"program_cfg_abstract_by_class\" {\n" +
      "compound=true;\n" +
      "node [shape=\"rect\"];\n\n" +
      clustersByClass.mkString("\n\n") + "\n\n" +
      (ctrlScopeEdges ++ actDepEdges ++ purpleArgEdges ++ blueEdges ++ redEdges).mkString("\n") + "\n" +
      "}"

    println(out)
    out
  }
}

// --- run and write file ---
val dot = CfgByClass.run()
import java.nio.file.{Files, Paths}
import java.nio.charset.StandardCharsets

val out = Paths.get("program_cfg_abstract_by_class.dot")
if (out.getParent != null) Files.createDirectories(out.getParent)
Files.write(out, dot.getBytes(StandardCharsets.UTF_8))
println("Wrote DOT to: " + out.toAbsolutePath)
